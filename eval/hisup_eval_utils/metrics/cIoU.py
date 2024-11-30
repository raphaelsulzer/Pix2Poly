"""
This is the code from https://github.com/zorzi-s/PolyWorldPretrainedNetwork.
@article{zorzi2021polyworld,
  title={PolyWorld: Polygonal Building Extraction with Graph Neural Networks in Satellite Images},
  author={Zorzi, Stefano and Bazrafkan, Shabab and Habenschuss, Stefan and Fraundorfer, Friedrich},
  journal={arXiv preprint arXiv:2111.15491},
  year={2021}
}
"""

from pycocotools.coco import COCO
from pycocotools import mask as cocomask
import numpy as np
import json
import argparse
from tqdm import tqdm

def calc_IoU(a, b):
    i = np.logical_and(a, b)
    u = np.logical_or(a, b)
    I = np.sum(i)
    U = np.sum(u)

    iou = I/(U + 1e-9)

    is_void = U == 0
    # is_void = U < (224**2)*(0.5/100)
    if is_void:
        return 1.0
    else:
        return iou

def compute_IoU_cIoU(input_json, gti_annotations):
    # Ground truth annotations
    coco_gti = COCO(gti_annotations)

    # Predictions annotations
    submission_file = json.loads(open(input_json).read())
    coco = COCO(gti_annotations)
    coco = coco.loadRes(submission_file)


    image_ids = coco.getImgIds(catIds=coco.getCatIds())
    bar = tqdm(image_ids)

    list_iou = []
    list_ciou = []
    pss = []
    rel_difs = []
    n_ratios = []
    for image_id in bar:

        img = coco.loadImgs(image_id)[0]

        annotation_ids = coco.getAnnIds(imgIds=img['id'])
        annotations = coco.loadAnns(annotation_ids)
        N = 0
        for _idx, annotation in enumerate(annotations):
            try:
                rle = cocomask.frPyObjects(annotation['segmentation'], img['height'], img['width'])
            except Exception:
                import ipdb; ipdb.set_trace()
            m = cocomask.decode(rle)
            if _idx == 0:
                mask = m.reshape((img['height'], img['width']))
                N = len(annotation['segmentation'][0]) // 2
            else:
                mask = mask + m.reshape((img['height'], img['width']))
                N = N + len(annotation['segmentation'][0]) // 2

        mask = mask != 0


        annotation_ids = coco_gti.getAnnIds(imgIds=img['id'])
        annotations = coco_gti.loadAnns(annotation_ids)
        N_GT = 0
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
                N_GT = len(annotation['segmentation'][0]) // 2
            else:
                mask_gti = mask_gti + m.reshape((img['height'], img['width']))
                N_GT = N_GT + len(annotation['segmentation'][0]) // 2

        mask_gti = mask_gti != 0

        ps = 1 - np.abs(N - N_GT) / (N + N_GT + 1e-9)
        rel_dif = np.abs(N - N_GT) / (N + N_GT + 1e-9)
        iou = calc_IoU(mask, mask_gti)
        list_iou.append(iou)
        list_ciou.append(iou * ps)
        pss.append(ps)
        rel_difs.append(rel_dif)
        if N_GT > 0:
            nr = N / N_GT
            n_ratios.append(nr)

        # if iou < 0.5:
        #     print(img['file_name'], img['id'])

        bar.set_description("iou: %2.4f, c-iou: %2.4f, ps:%2.4f, rd:%2.4f" % (np.mean(list_iou), np.mean(list_ciou), np.mean(pss), np.mean(rel_difs)))
        bar.refresh()

    print("Done!")
    print("Mean IoU: ", np.mean(list_iou))
    print("Mean C-IoU: ", np.mean(list_ciou))
    print("Mean N-Relative Difference: ", np.mean(rel_difs))
    print("Mean N-Ratio: ", np.mean(n_ratios))



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gt-file", default="")
    parser.add_argument("--dt-file", default="")
    args = parser.parse_args()

    gt_file = args.gt_file
    dt_file = args.dt_file
    compute_IoU_cIoU(input_json=dt_file,
                    gti_annotations=gt_file)
