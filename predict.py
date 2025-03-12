# disable annoying transfromers and albumentation warnings
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)
import os
os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'

import time
import json
from tqdm import tqdm
import numpy as np
import cv2
import matplotlib.pyplot as plt
import hydra
from omegaconf import OmegaConf

import torch
import logging
from torchvision.utils import make_grid
from torchmetrics.classification import BinaryJaccardIndex, BinaryAccuracy
import torch.multiprocessing
torch.multiprocessing.set_sharing_strategy("file_system")

from tokenizer import Tokenizer
from utils import seed_everything, postprocess, permutations_to_polygons, compute_dynamic_cfg_vars, test_generate
from models.model import Encoder, Decoder, EncoderDecoder
from datasets.build_datasets import get_val_loader

from lidar_poly_dataset.utils import generate_coco_ann
from lidar_poly_dataset.misc import make_logger

class Predicter:
    def __init__(self, cfg, verbosity=logging.INFO):
        self.cfg = cfg
        self.logger = make_logger("Prediction",level=verbosity,filepath=os.path.join(cfg.output_dir, 'predict.log'))


    def get_model(self,tokenizer):
        
        encoder = Encoder(model_name=self.cfg.model.type, pretrained=True, out_dim=256)
        decoder = Decoder(
            vocab_size=tokenizer.vocab_size,
            encoder_len=self.cfg.model.num_patches,
            dim=256,
            num_heads=8,
            num_layers=6,
            max_len=self.cfg.model.tokenizer.max_len,
            pad_idx=self.cfg.model.tokenizer.pad_idx,
        )
        model = EncoderDecoder(
            encoder=encoder,
            decoder=decoder,
            n_vertices=self.cfg.model.tokenizer.n_vertices,
            sinkhorn_iterations=self.cfg.model.sinkhorn_iterations
        )
        model.to(self.cfg.device)
        model.eval()
        
        return model

    def make_pixel_mask_from_prediction(self, x, batch_polygons):
        B, C, H, W = x.shape

        polygons_mask = np.zeros((B, 1, H, W))
        for b in range(len(batch_polygons)):
            for c in range(len(batch_polygons[b])):
                poly = batch_polygons[b][c]
                poly = poly[poly[:, 0] != self.cfg.model.tokenizer.pad_idx]
                cnt = np.flip(np.int32(poly.cpu()), 1)
                if len(cnt) > 0:
                    cv2.fillPoly(polygons_mask[b, 0], pts=[cnt], color=1.)
        return torch.from_numpy(polygons_mask)
        

    def plot_prediction_as_mask(self, polygon_mask, y_mask, outfile):

        pred_grid = make_grid(polygon_mask).permute(1, 2, 0)
        gt_grid = make_grid(y_mask).permute(1, 2, 0)
        plt.subplot(211), plt.imshow(pred_grid) ,plt.title("Predicted Polygons") ,plt.axis('off')
        plt.subplot(212), plt.imshow(gt_grid) ,plt.title("Ground Truth") ,plt.axis('off')
        
        os.makedirs(os.path.dirname(outfile), exist_ok=True)
        plt.savefig(outfile)
        plt.close()


    def plot_prediction_as_polygons(self, polygons, img):
        
        if not len(polygons):
            return
        
        from lidar_poly_dataset.utils import plot_polygons
        img = img.permute(1, 2, 0).cpu().numpy()
        plot_polygons(polygons, img)
        

    def run(self):
        seed_everything(42)

        tokenizer = Tokenizer(
            num_classes=1,
            num_bins=self.cfg.model.tokenizer.num_bins,
            width=self.cfg.model.input_width,
            height=self.cfg.model.input_height,
            max_len=self.cfg.model.tokenizer.max_len
        )
        
        compute_dynamic_cfg_vars(self.cfg,tokenizer)

        val_loader = get_val_loader(self.cfg,tokenizer)
        model = self.get_model(tokenizer)

        checkpoint = torch.load(self.cfg.checkpoint, map_location=self.cfg.device)
        model.load_state_dict(checkpoint['state_dict'])
        epoch = checkpoint['epochs_run']

        self.logger.info(f"Model loaded from epoch: {epoch}")

        mean_iou_metric = BinaryJaccardIndex()
        mean_acc_metric = BinaryAccuracy()


        with torch.no_grad():
            cumulative_miou = []
            cumulative_macc = []
            speed = []
            predictions = []
            for x, y_mask, y_corner_mask, y, y_perm, idx in tqdm(val_loader):
                all_coords = []
                all_confs = []
                t0 = time.time()

                batch_preds, batch_confs, perm_preds = test_generate(
                    model.encoder,
                    x,
                    tokenizer,
                    max_len=self.cfg.model.tokenizer.generation_steps,
                    top_k=0,
                    top_p=1,
                )
                
                speed.append(time.time() - t0)
                vertex_coords, confs = postprocess(batch_preds, batch_confs, tokenizer)

                all_coords.extend(vertex_coords)
                all_confs.extend(confs)

                coords = []
                for i in range(len(all_coords)):
                    if all_coords[i] is not None:
                        coord = torch.from_numpy(all_coords[i])
                    else:
                        coord = torch.tensor([])

                    padd = torch.ones((cfg.model.tokenizer.n_vertices - len(coord), 2)).fill_(cfg.model.tokenizer.pad_idx)
                    coord = torch.cat([coord, padd], dim=0)
                    coords.append(coord)
                batch_polygons = permutations_to_polygons(perm_preds, coords, out='torch')  # [0, 224]     

                for ip, pp in enumerate(batch_polygons):
                    polys = []
                    for p in pp:
                        p = torch.fliplr(p)
                        p = p[p[:, 0] != self.cfg.model.tokenizer.pad_idx]
                        if len(p) > 0:
                            polys.append(p)
                    predictions.extend(generate_coco_ann(polys,idx[ip].item()))

                polygons_mask = self.make_pixel_mask_from_prediction(x,batch_polygons,self.cfg)
                batch_miou = mean_iou_metric(polygons_mask, y_mask)
                batch_macc = mean_acc_metric(polygons_mask, y_mask)

                self.plot_prediction_as_polygons(polys, x[-1])
                # outfile = os.path.join(cfg.output_dir, "images", f"predictions_{idx}.png")
                # plot_prediction_as_mask(polygons_mask, y_mask, outfile)

                cumulative_miou.append(batch_miou)
                cumulative_macc.append(batch_macc)

            self.logger.info("Average model speed: ", np.mean(speed) / self.cfg.model.batch_size, " [s / image]")

            self.logger.info(f"Average Mean IOU: {torch.tensor(cumulative_miou).nanmean()}")
            self.logger.info(f"Average Mean Acc: {torch.tensor(cumulative_macc).nanmean()}")

        checkpoint_name = os.path.split(self.cfg.checkpoint)[-1][:-4]
        prediction_outfile = os.path.join(self.cfg.output_dir, f"predictions_{checkpoint_name}.json")
        with open(prediction_outfile, "w") as fp:
            fp.write(json.dumps(predictions))



@hydra.main(config_path="conf", config_name="config", version_base="1.3")
def main(cfg):
    OmegaConf.resolve(cfg)
    print("\nConfiguration:")
    print(OmegaConf.to_yaml(cfg))

    pp = Predicter(cfg)
    pp.run()

if __name__ == "__main__":
    main()
