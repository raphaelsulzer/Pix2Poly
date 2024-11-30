from pycocotools.coco import COCO
from pycocotools import mask as cocomask
import numpy as np
import json
import argparse
from tqdm import tqdm

import torch
import torchvision
import cv2

from eval.hisup_eval_utils.metrics.cIoU import calc_IoU
from torchmetrics.functional.classification import binary_accuracy, binary_f1_score
# from sklearn.metrics import accuracy_score, f1_score
from eval.topdig_eval_utils.metrics.topdig_authors_metrics import performMetrics


def calc_f1score(mask: np.ndarray, mask_gti: np.ndarray):
    union = np.logical_or(mask, mask_gti)
    U = np.sum(union)
    is_void = U == 0
    # is_void = U < (224**2)*(0.5/100)
    mask = torch.from_numpy(mask)
    mask_gti = torch.from_numpy(mask_gti)

    if is_void:
        return 1.0
    else:
        return binary_f1_score(preds=mask, target=mask_gti)
        # return f1_score(mask_gti, mask)


def calc_acc(mask: np.ndarray, mask_gti: np.ndarray):
    union = np.logical_or(mask, mask_gti)
    U = np.sum(union)
    is_void = U == 0
    # is_void = U < (224**2)*(0.5/100)
    mask = torch.from_numpy(mask)
    mask_gti = torch.from_numpy(mask_gti)

    if is_void:
        return 1.0
    else:
        return binary_accuracy(preds=mask, target=mask_gti)
        # return accuracy_score(mask_gti, mask)


def compute_mask_metrics(input_json, gti_annotations):
    # Ground truth annotations
    coco_gti = COCO(gti_annotations)

    # Predictions annotations
    submission_file = json.loads(open(input_json).read())
    coco = COCO(gti_annotations)
    coco = coco.loadRes(submission_file)


    image_ids = coco.getImgIds(catIds=coco.getCatIds())
    bar = tqdm(image_ids)

    buffer_thickness = 5  # dilation factor same as that used in TopDiG.

    list_acc = []
    list_f1 = []
    list_iou = []

    list_acc_topo = []
    list_f1_topo = []
    list_iou_topo = []

    city_wise_iou_topo = {
        'austin': [],
        'chicago': [],
        'kitsap': [],
        'tyrol-w': [],
        'vienna': []
    }
    for image_id in bar:

        img = coco.loadImgs(image_id)[0]

        # Predictions
        annotation_ids = coco.getAnnIds(imgIds=img['id'])
        annotations = coco.loadAnns(annotation_ids)
        topo_mask = np.zeros((img['height'], img['width']))
        poly_lines = []
        for _idx, annotation in enumerate(annotations):
            try:
                rle = cocomask.frPyObjects(annotation['segmentation'], img['height'], img['width'])
            except Exception:
                import ipdb; ipdb.set_trace()
            m = cocomask.decode(rle)
            if _idx == 0:
                mask = m.reshape((img['height'], img['width']))
            else:
                mask = mask + m.reshape((img['height'], img['width']))
            for ann in annotation['segmentation']:
                ann = np.array(ann).reshape(-1, 2)
                # if 'vienna' in img['file_name']:
                #     ann[:, 1] -= 5.
                ann = np.round(ann).astype(np.int32)
                poly_lines.append(ann)
        cv2.polylines(topo_mask, poly_lines, isClosed=True, color=1., thickness=buffer_thickness)

        mask = mask != 0
        topo_mask = (topo_mask != 0).astype(np.float32)


        # Ground truth
        annotation_ids = coco_gti.getAnnIds(imgIds=img['id'])
        annotations = coco_gti.loadAnns(annotation_ids)
        topo_mask_gt = np.zeros((img['height'], img['width']))
        poly_lines_gt = []
        for _idx, annotation in enumerate(annotations):
            if any(annotation['segmentation']):
                rle = cocomask.frPyObjects(annotation['segmentation'], img['height'], img['width'])
                m = cocomask.decode(rle)
            else:
                annotation['segmentation'] = [[]]
                m = np.zeros((img['height'], img['width']))
            if m.ndim > 2:
                m = np.clip(0, 1, m.sum(axis=-1))
            if _idx == 0:
                mask_gti = m.reshape((img['height'], img['width']))
            else:
                mask_gti = mask_gti + m.reshape((img['height'], img['width']))
            for ann in annotation['segmentation']:
                ann = np.array(ann).reshape(-1, 2)
                ann = np.round(ann).astype(np.int32)
                poly_lines_gt.append(ann)
        cv2.polylines(topo_mask_gt, poly_lines_gt, isClosed=True, color=1., thickness=buffer_thickness)

        mask_gti = mask_gti != 0
        topo_mask_gt = (topo_mask_gt != 0).astype(np.float32)

        # import code; code.interact(local=locals())

        mask_stats = performMetrics(mask, mask_gti)
        topo_stats = performMetrics(topo_mask, topo_mask_gt)


        pacc = mask_stats['Pixel Accuracy']
        list_acc.append(pacc)
        f1score = mask_stats['F1-score']
        list_f1.append(f1score)
        iou = mask_stats['IoU']
        list_iou.append(iou)

        pacc_topo = topo_stats['Pixel Accuracy']
        list_acc_topo.append(pacc_topo)
        f1score_topo = topo_stats['F1-score']
        list_f1_topo.append(f1score_topo)
        iou_topo = topo_stats['IoU']
        list_iou_topo.append(iou_topo)

        # im_city = img['file_name'].split('-')[0]
        # im_city = ''.join([i for i in im_city if not i.isdigit()])
        # if im_city == 'tyrol':
        #     im_city = 'tyrol-w'

        # city_wise_iou_topo[im_city].append(iou_topo)

        # if iou < 0.5:
        #     print(img['file_name'], img['id'])

        # if iou_topo <= 0.5:
        #     topo_vis = np.zeros((1, 3, img['width'], img['height']))
        #     topo_vis[0, 0] = topo_mask_gt
        #     topo_vis[0, 1] = topo_mask
        #     topo_vis[0, 2] = topo_mask
        #     # topo_vis = np.concatenate([topo_mask_gt[None, ...], topo_mask[None, ...]])[:, None, :, :]
        #     topo_vis = torch.from_numpy(topo_vis)
        #     torchvision.utils.save_image(topo_vis, f'scratch/vis_inria170_10_lowTopoIou/{img["file_name"].split(".")[0]}.png')

        bar.set_description("iou: %2.4f, p-acc: %2.4f, f1:%2.4f, iou-topo: %2.4f, p-acc-topo: %2.4f, f1-topo:%2.4f " % (np.mean(list_iou), np.mean(list_acc), np.mean(list_f1), np.mean(list_iou_topo), np.mean(list_acc_topo), np.mean(list_f1_topo)))
        bar.refresh()

    print("Done!")
    print("Mean IoU: ", np.mean(list_iou))
    print("Mean P-Acc: ", np.mean(list_acc))
    print("Mean F1-Score: ", np.mean(list_f1))
    print("Mean IoU-Topo: ", np.mean(list_iou_topo))
    print("Mean P-Acc-Topo: ", np.mean(list_acc_topo))
    print("Mean F1-Score-Topo: ", np.mean(list_f1_topo))

    for k, v in city_wise_iou_topo.items():
        print(f'{k}: {np.mean(v)}')



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gt-file", default="")
    parser.add_argument("--dt-file", default="")
    args = parser.parse_args()

    gt_file = args.gt_file
    dt_file = args.dt_file
    compute_mask_metrics(input_json=dt_file,
                    gti_annotations=gt_file)
