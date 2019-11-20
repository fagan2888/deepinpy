#!/usr/bin/env python

import numpy as np
import torch
import tqdm

from deepinpy.utils import utils
from  deepinpy.opt import ConjGrad
from deepinpy import opt
from deepinpy.forwards import MultiChannelMRI
from deepinpy.models import ResNet5Block, ResNet
from deepinpy.recons import Recon

class DeepBasisPursuitRecon(Recon):

    def __init__(self, args):
        super(DeepBasisPursuitRecon, self).__init__(args)
        self.l2lam = torch.nn.Parameter(torch.tensor(args.l2lam_init))
        self.num_admm = args.num_admm

        if args.network == 'ResNet5Block':
            self.denoiser = ResNet5Block(num_filters=args.latent_channels, filter_size=7, batch_norm=args.batch_norm)
        elif args.network == 'ResNet':
            self.denoiser = ResNet(latent_channels=args.latent_channels, num_blocks=args.num_blocks, kernel_size=7, batch_norm=args.batch_norm)

        self.debug_level = 0

    def batch(self, data):

        maps = data['maps']
        masks = data['masks']
        inp = data['out']

        self.A = MultiChannelMRI(maps, masks, l2lam=0., img_shape=data['imgs'].shape, use_sigpy=self.use_sigpy, noncart=self.noncart)
        self.x_adj = self.A.adjoint(inp)

    def forward(self, y):
        eps = opt.ip_batch(self.A.maps.shape[1] * self.A.mask.sum((1, 2))).sqrt() * self.stdev
        x = self.A.adjoint(y)
        z = self.A(x)
        z_old = z
        u = z.new_zeros(z.shape)

        x.requires_grad = False
        z.requires_grad = False
        z_old.requires_grad = False
        u.requires_grad = False

        self.num_cg = np.zeros((self.num_unrolls,self.num_admm,))

        for i in range(self.num_unrolls):
            r = self.denoiser(x)

            for j in range(self.num_admm):

                rhs = self.l2lam * self.A.adjoint(z - u) + r
                fun = lambda xx: self.l2lam * self.A.normal(xx) + xx
                cg_op = ConjGrad(rhs, fun, max_iter=self.cg_max_iter, eps=self.eps, verbose=False)
                x = cg_op.forward(x)
                n_cg = cg_op.num_cg
                self.num_cg[i, j] = n_cg

                Ax_plus_u = self.A(x) + u
                z_old = z
                z = y + opt.l2ball_proj_batch(Ax_plus_u - y, eps)
                u = Ax_plus_u - z

                # check ADMM convergence
                Ax = self.A(x)
                r_norm = opt.ip_batch(Ax-z).sqrt()
                s_norm = opt.ip_batch(self.l2lam * self.A.adjoint(z - z_old)).sqrt()
                if (r_norm + s_norm).max() < 1E-2:
                    if self.debug_level > 0:
                        tqdm.tqdm.write('stopping early, a={}'.format(a))
                    break
        return x

    def get_metadata(self):
        return {
                'num_cg': self.num_cg.ravel(),
                }
