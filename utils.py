import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import torch
import torchvision
from scipy.optimize import linear_sum_assignment
from torchmetrics.functional.classification import binary_accuracy, binary_jaccard_index
from tqdm import tqdm
from transformers.generation.utils import top_k_top_p_filtering


def seed_everything(seed: int = 1234) -> None:
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True


def save_checkpoint(
    state: Dict[str, Any],
    folder: Union[str, Path] = "logs/checkpoint/run1",
    filename: str = "my_checkpoint.pth.tar",
) -> None:
    print("=> Saving checkpoint")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    torch.save(state, folder / filename)


def load_checkpoint(
    checkpoint: Dict[str, Any],
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler._LRScheduler,
) -> int:
    print("=> Loading checkpoint")
    model.load_state_dict(checkpoint["state_dict"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    scheduler.load_state_dict(checkpoint["scheduler"])

    return checkpoint["epochs_run"]


def generate_square_subsequent_mask(sz: int, device: torch.device) -> torch.Tensor:
    mask = (torch.triu(torch.ones((sz, sz), device=device)) == 1).transpose(0, 1)

    mask = mask.float().masked_fill(mask==0, float('-inf')).masked_fill(mask==1, float(0.0))

    return mask


def create_mask(
    tgt: torch.Tensor, pad_idx: int, device: torch.device
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    tgt shape: (N, L)
    """

    tgt_seq_len = tgt.size(1)
    tgt_mask = generate_square_subsequent_mask(tgt_seq_len, device)
    tgt_padding_mask = (tgt == pad_idx)

    return tgt_mask, tgt_padding_mask


class AverageMeter:
    def __init__(self, name: str = "Metric") -> None:
        self.name = name
        self.reset()

    def reset(self) -> None:
        self.avg, self.sum, self.count = [0]*3

    def update(self, val: float, count: int = 1) -> None:
        self.count += count
        self.sum += val * count
        self.avg = self.sum / self.count

    def __repr__(self) -> str:
        text = f"{self.name}: {self.avg:.4f}"
        return text


def get_lr(optimizer: torch.optim.Optimizer) -> float:
    for param_group in optimizer.param_groups:
        return param_group['lr']


def scores_to_permutations(scores: torch.Tensor) -> torch.Tensor:
    """
    Input a batched array of scores and returns the hungarian optimized 
    permutation matrices.
    """
    B, N, N = scores.shape

    scores = scores.detach().cpu().numpy()
    perm = np.zeros_like(scores)
    for b in range(B):
        r, c = linear_sum_assignment(-scores[b])
        perm[b,r,c] = 1
    return torch.tensor(perm)


# TODO: add permalink to polyworld repo
def permutations_to_polygons(
    perm: torch.Tensor, graph: torch.Tensor, out: str = "torch"
) -> List[List[torch.Tensor]]:
    B, N, N = perm.shape
    device = perm.device

    def bubble_merge(poly):
        s = 0
        P = len(poly)
        while s < P:
            head = poly[s][-1]

            t = s+1
            while t < P:
                tail = poly[t][0]
                if head == tail:
                    poly[s] = poly[s] + poly[t][1:]
                    del poly[t]
                    poly = bubble_merge(poly)
                    P = len(poly)
                t += 1
            s += 1
        return poly

    diag = torch.logical_not(perm[:,range(N),range(N)])
    batch = []
    for b in range(B):
        b_perm = perm[b]
        b_graph = graph[b]
        b_diag = diag[b]

        idx = torch.arange(N, device=perm.device)[b_diag]

        if idx.shape[0] > 0:
            # If there are vertices in the batch

            b_perm = b_perm[idx,:]
            b_graph = b_graph[idx,:]
            b_perm = b_perm[:,idx]

            first = torch.arange(idx.shape[0]).unsqueeze(1).to(device=device)
            second = torch.argmax(b_perm, dim=1).unsqueeze(1)

            polygons_idx = torch.cat((first, second), dim=1).tolist()
            polygons_idx = bubble_merge(polygons_idx)

            batch_poly = []
            for p_idx in polygons_idx:
                if out == 'torch':
                    batch_poly.append(b_graph[p_idx,:])
                elif out == 'numpy':
                    batch_poly.append(b_graph[p_idx,:].cpu().numpy())
                elif out == 'list':
                    g = b_graph[p_idx,:] * 300 / 320
                    g[:,0] = -g[:,0]
                    g = torch.fliplr(g)
                    batch_poly.append(g.tolist())
                elif out == 'coco':
                    g = b_graph[p_idx,:]# * CFG.IMG_SIZE / CFG.INPUT_WIDTH
                    g = torch.fliplr(g)
                    batch_poly.append(g.view(-1).tolist())
                elif out == 'inria-torch':
                    batch_poly.append(b_graph[p_idx,:])
                else:
                    print("Indicate a valid output polygon format")
                    exit()

            batch.append(batch_poly)

        else:
            # If the batch has no vertices
            batch.append([])

    return batch


def test_generate(
    encoder: torch.nn.Module,
    model_taking_encoded_images: torch.nn.Module,
    x: torch.Tensor,
    tokenizer: Any,
    max_len: int = 50,
    top_k: int = 0,
    top_p: float = 1.0,
    device: Union[str, torch.device] = "cuda",
) -> Tuple[torch.Tensor, List[torch.Tensor], torch.Tensor]:
    x = x.to(device)
    batch_preds = (
        torch.ones((x.size(0), 1), device=device).fill_(tokenizer.BOS_code).long()
    )
    confs = []

    if top_k != 0 or top_p != 1:

        def sample(preds):
            return torch.softmax(preds, dim=-1).multinomial(num_samples=1).view(-1, 1)

    else:

        def sample(preds):
            return torch.softmax(preds, dim=-1).argmax(dim=-1).view(-1, 1)

    encoder_out: None | torch.Tensor = None

    with torch.no_grad():
        encoder_out = encoder(x) if encoder_out is None else encoder_out.to(device)
        for i in tqdm(range(max_len), desc="tokens"):
            if isinstance(model_taking_encoded_images, torch.nn.parallel.DistributedDataParallel):
                preds, feats = model_taking_encoded_images.module.predict(encoder_out, batch_preds)
            else:
                preds, feats = model_taking_encoded_images.predict(encoder_out, batch_preds)
            preds = top_k_top_p_filtering(preds, top_k=top_k, top_p=top_p)  # if top_k and top_p are set to default, this line does nothing.
            if i % 2 == 0:
                confs_ = torch.softmax(preds, dim=-1).sort(axis=-1, descending=True)[0][:, 0].cpu()
                confs.append(confs_)
            preds = sample(preds)
            batch_preds = torch.cat([batch_preds, preds], dim=1)
        encoder_out = encoder_out.to(torch.device('cpu'))
        torch.cuda.empty_cache()

        print(torch.cuda.memory_summary())

        if isinstance(model_taking_encoded_images.encoderdecoder, torch.nn.parallel.DistributedDataParallel):
            perm_preds = model_taking_encoded_images.encoderdecoder.module.scorenet1(feats) + torch.transpose(model_taking_encoded_images.encoderdecoder.module.scorenet2(feats), 1, 2)
        else:
            perm_preds = model_taking_encoded_images.encoderdecoder.scorenet1(feats) + torch.transpose(model_taking_encoded_images.encoderdecoder.scorenet2(feats), 1, 2)

        perm_preds = scores_to_permutations(perm_preds)

    return batch_preds.cpu(), confs, perm_preds


def postprocess(
    batch_preds: torch.Tensor, batch_confs: List[torch.Tensor], tokenizer: Any
) -> Tuple[List[Optional[np.ndarray]], List[Optional[List[float]]]]:
    EOS_idxs = (batch_preds == tokenizer.EOS_code).float().argmax(dim=-1)
    ## sanity check
    invalid_idxs = ((EOS_idxs - 1) % 2 != 0).nonzero().view(-1)
    EOS_idxs[invalid_idxs] = 0

    all_coords = []
    all_confs = []
    for i, EOS_idx in enumerate(EOS_idxs.tolist()):
        if EOS_idx == 0:
            all_coords.append(None)
            all_confs.append(None)
            continue
        coords = tokenizer.decode(batch_preds[i, :EOS_idx+1])
        confs = [round(batch_confs[j][i].item(), 3) for j in range(len(coords))]

        all_coords.append(coords)
        all_confs.append(confs)

    return all_coords, all_confs


def save_single_predictions_as_images(
    loader: torch.utils.data.DataLoader,
    model: torch.nn.Module,
    tokenizer: Any,
    epoch: int,
    writer: Any,
    n_vertices: int,
    generation_steps: int,
    folder: Union[str, Path] = "saved_outputs/",
    device: Union[str, torch.device] = "cuda",
) -> Dict[str, torch.Tensor]:
    print("=> Saving val predictions...")
    if not os.path.exists(folder):
        print("==> Creating output subdirectory...")
        os.makedirs(folder)

    model.eval()

    all_coords = []
    all_confs = []

    with torch.no_grad():
        loader_iterator = iter(loader)
        idx, (x, y_mask, y_corner_mask, y, y_perm) = 0, next(loader_iterator)
        batch_preds, batch_confs, perm_preds = test_generate(
            model,
            x,
            tokenizer,
            max_len=generation_steps,  # Changed from CFG.generation_steps
            top_k=0,
            top_p=1,
            device=device,
        )
        vertex_coords, confs = postprocess(batch_preds, batch_confs, tokenizer)

        all_coords.extend(vertex_coords)
        all_confs.extend(confs)

        coords = []
        for i in range(len(all_coords)):
            if all_coords[i] is not None:
                coord = torch.from_numpy(all_coords[i])
            else:
                coord = torch.tensor([])
            padd = torch.ones((n_vertices - len(coord), 2)).fill_(
                tokenizer.PAD_code
            )  # Changed from CFG.N_VERTICES
            coord = torch.cat((coord, padd), dim=0)
            coords.append(coord)
        batch_polygons = permutations_to_polygons(perm_preds, coords, out='torch')  # list of polygon coordinate tensors

    B, C, H, W = x.shape
    # Write predicted vertices as mask to disk.
    vertex_mask = np.zeros((B, 1, H, W))
    for b in range(len(all_coords)):
        if all_coords[b] is not None:
            print("Vertices found!")
            for i in range(len(all_coords[b])):
                coord = all_coords[b][i]
                cx, cy = coord
                cv2.circle(vertex_mask[b, 0], (int(cy), int(cx)), 0, 255, -1)
    vertex_mask = torch.from_numpy(vertex_mask)
    if not os.path.exists(os.path.join(folder, 'corners_mask')):
        os.makedirs(os.path.join(folder, 'corners_mask'))
    vertex_pred_vis = torch.zeros_like(x)
    for b in range(B):
        vertex_pred_vis[b] = torchvision.utils.draw_segmentation_masks(
            (x[b]*255).to(dtype=torch.uint8),
            torch.zeros_like(x[b, 0]).bool()
        )
    vertex_pred_vis = vertex_pred_vis.cpu().numpy().astype(np.uint8)
    for b in range(len(all_coords)):
        if all_coords[b] is not None:
            for i in range(len(all_coords[b])):
                coord = all_coords[b][i]
                cx, cy = coord
                cv2.circle(vertex_pred_vis[b, 0], (int(cy), int(cx)), 3, 255, -1)
    vertex_pred_vis = torch.from_numpy(vertex_pred_vis)
    torchvision.utils.save_image(
        vertex_pred_vis.float()/255, f"{folder}/corners_mask/corners_mask_{b}_{epoch}.png"
    )

    # Write predicted polygons as mask to disk.
    polygons = np.zeros((B, 1, H, W))
    for b in range(B):
        for c in range(len(batch_polygons[b])):
            poly = batch_polygons[b][c]
            poly = poly[poly[:, 0] != tokenizer.PAD_code]
            cnt = np.flip(np.int32(poly.cpu()), 1)
            if len(cnt) > 0:
                cv2.fillPoly(polygons[b, 0], pts=[cnt], color=1.)
    polygons = torch.from_numpy(polygons)
    if not os.path.exists(os.path.join(folder, 'pred_polygons')):
        os.makedirs(os.path.join(folder, 'pred_polygons'))
    poly_out = torch.zeros_like(x)
    for b in range(B):
        poly_out[b] = torchvision.utils.draw_segmentation_masks(
            (x[b]*255).to(dtype=torch.uint8),
            polygons[b, 0].bool()
        )
    poly_out = poly_out.cpu().numpy().astype(np.uint8)
    for b in range(len(all_coords)):
        if all_coords[b] is not None:
            for i in range(len(all_coords[b])):
                coord = all_coords[b][i]
                cx, cy = coord
                cv2.circle(poly_out[b, 0], (int(cy), int(cx)), 2, 255, -1)
    poly_out = torch.from_numpy(poly_out)
    torchvision.utils.save_image(
        poly_out.float()/255, f"{folder}/pred_polygons/polygons_{idx}_{epoch}.png"
    )

    batch_miou = binary_jaccard_index(polygons, y_mask)
    batch_biou = binary_jaccard_index(polygons, y_mask, ignore_index=0)
    batch_macc = binary_accuracy(polygons, y_mask)
    batch_bacc = binary_accuracy(polygons, y_mask, ignore_index=0)

    writer.add_scalar('Val_Metrics/Mean_IoU', batch_miou, epoch)
    writer.add_scalar('Val_Metrics/Building_IoU', batch_biou, epoch)
    writer.add_scalar('Val_Metrics/Mean_Accuracy', batch_macc, epoch)
    writer.add_scalar('Val_Metrics/Building_Accuracy', batch_bacc, epoch)

    metrics_dict = {
        "miou": batch_miou,
        "biou": batch_biou,
        "macc": batch_macc,
        "bacc": batch_bacc
    }

    torchvision.utils.save_image(x, f"{folder}/image_{idx}.png")
    ymask_out = torch.zeros_like(x)
    for b in range(B):
        ymask_out[b] = torchvision.utils.draw_segmentation_masks(
            (x[b]*255).to(dtype=torch.uint8),
            y_mask[b, 0].bool()
        )
    ymask_out = ymask_out.cpu().numpy().astype(np.uint8)
    gt_corner_coords, _ = postprocess(y, batch_confs, tokenizer)
    for b in range(B):
        for corner in gt_corner_coords[b]:
            cx, cy = corner
            cv2.circle(ymask_out[b, 0], (int(cy), int(cx)), 3, 255, -1)
    ymask_out = torch.from_numpy(ymask_out)
    torchvision.utils.save_image(ymask_out/255., f"{folder}/gt_mask_{idx}.png")
    torchvision.utils.save_image(y_corner_mask*255, f"{folder}/gt_corners_{idx}.png")
    torchvision.utils.save_image(y_perm[:, None, :, :]*255, f"{folder}/gt_perm_matrix_{idx}.png")

    return metrics_dict