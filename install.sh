#!/bin/bash
set -e

# Local variables
PROJECT_NAME=pix2poly
PYTHON=3.11.11

# Installation script for Anaconda3 environments
echo "____________ Pick conda install _____________"
echo
# Recover the path to conda on your machine
CONDA_DIR=`realpath /opt/miniconda3`
if (test -z $CONDA_DIR) || [ ! -d $CONDA_DIR ]
then
  CONDA_DIR=`realpath ~/anaconda3`
fi

while (test -z $CONDA_DIR) || [ ! -d $CONDA_DIR ]
do
    echo "Could not find conda at: "$CONDA_DIR
    read -p "Please provide you conda install directory: " CONDA_DIR
    CONDA_DIR=`realpath $CONDA_DIR`
done

echo "Using conda found at: ${CONDA_DIR}/etc/profile.d/conda.sh"
source ${CONDA_DIR}/etc/profile.d/conda.sh
echo
echo


echo "________________ Installation _______________"
echo

# Create a conda environment from yml
conda create -y --name $PROJECT_NAME python=$PYTHON

# Activate the env
source ${CONDA_DIR}/etc/profile.d/conda.sh
conda activate ${PROJECT_NAME}

# dependencies
pip install torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 --index-url https://download.pytorch.org/whl/cu118
conda install conda-forge::transformers=4.32.1 -y
conda install conda-forge::pycocotools -y
conda install conda-forge::torchmetrics -y
conda install conda-forge::tensorboard -y
conda install conda-forge::wandb -y
conda install conda-forge::timm=0.9.12 -y
pip install matplotlib==3.7.0
pip install -r requirements.txt

# problem with torch:tms? do this:
# https://github.com/huggingface/diffusers/issues/8958#issuecomment-2253055261

## for inria_to_coco.py
conda install conda-forge::imagecodecs -y

## for lidar_poly_dataloader
conda install conda-forge::gcc_linux-64=10 conda-forge::gxx_linux-64=10 -y # otherwise copclib install bugs
pip install copclib
conda install conda-forge::colorlog -y
conda install conda-forge::descartes=1.1.0 -y