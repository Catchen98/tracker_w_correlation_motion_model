# --------------------------------------------------------
# Tensorflow Faster R-CNN
# Licensed under The MIT License [see LICENSE for details]
# Written by Zheqi he, Xinlei Chen, based on code from Ross Girshick
# --------------------------------------------------------
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import frcnn
from model.test import test_net
from model.config import cfg, cfg_from_file, cfg_from_list
from datasets.factory import get_imdb
import argparse
import pprint
import time, os, sys

from nets.vgg16 import vgg16
from nets.resnet_v1 import resnetv1
#from nets.mobilenet_v1 import mobilenetv1

import torch

def frcnn_test(args):
    """
    args = {#'imdb_name':imdb_name,
            'imdbtest_name':imdbtest_name,
            'net':network,
            'cfg_file':None,
            'set_cfgs':None,
            'tag':tag,
            'comp_mode':comp,
            'max_per_image':max_per_image,
            'output_dir':output_dir,
            'model':model}
    """

    if args['cfg_file']:
        cfg_from_file(args['cfg_file'])
    if args['set_cfgs']:
        cfg_from_list(args['set_cfgs'])

    print('Using config:')
    pprint.pprint(cfg)

    #filename = os.path.splitext(os.path.basename(args['model']))[0]

    imdb_test = get_imdb(args['imdbtest_name'])
    imdb_test.competition_mode(args['comp_mode'])

    # load network
    if args['net'] == 'vgg16':
        net = vgg16(batch_size=1)
    elif args['net'] == 'res50':
        net = resnetv1(batch_size=1, num_layers=50)
    elif args['net'] == 'res101':
        net = resnetv1(batch_size=1, num_layers=101)
    elif args['net'] == 'res152':
        net = resnetv1(batch_size=1, num_layers=152)
    else:
        raise NotImplementedError

    # load model
    net.create_architecture(imdb_test.num_classes, tag='default',
        anchor_scales=cfg.ANCHOR_SCALES,
        anchor_ratios=cfg.ANCHOR_RATIOS)

    net.eval()
    net.cuda()


    print(('Loading model check point from {:s}').format(args['model']))
    net.load_state_dict(torch.load(args['model']))
    print('Loaded.')


    test_net(net, imdb_test, args['output_dir'], max_per_image=args['max_per_image'])