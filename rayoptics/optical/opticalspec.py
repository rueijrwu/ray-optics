#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright © 2018 Michael J. Hayford
""" Container class for optical usage information

Created on Thu Jan 25 11:01:04 2018

@author: Michael J. Hayford
"""

import math
import numpy as np
from numpy.linalg import norm
import pandas as pd

from rayoptics.util.misc_math import normalize
from rayoptics.optical.firstorder import compute_first_order
from rayoptics.optical.model_constants import ht, slp, aoi
from . import raytrace as rt
import rayoptics.util.colour_system as cs
srgb = cs.cs_srgb


class OpticalSpecs:
    """ Container class for optical usage information

    Contains optical usage information to specify the aperture, field of view,
    spectrum and focal position.

    It also supports model ray tracing in terms of relative
    aperture and field.

    It maintains a repository of paraxial data.
    """
    def __init__(self):
        self.spectral_region = WvlSpec()
        self.pupil = PupilSpec()
        self.field_of_view = FieldSpec()
        self.defocus = FocusRange(0.0)
        self.parax_data = None

    def __json_encode__(self):
        attrs = dict(vars(self))
        del attrs['parax_data']
        return attrs

    def set_from_list(self, dl):
        self.spectral_region = dl[0]
        self.pupil = dl[1]
        self.field_of_view = dl[2]

    def update_model(self, seq_model):
        self.pupil.update_model(seq_model)
        self.field_of_view.update_model(seq_model)
        stop = seq_model.stop_surface
        wvl = self.spectral_region.central_wvl()
        if not hasattr(self, 'defocus'):
            self.defocus = FocusRange(0.0)

        self.parax_data = compute_first_order(seq_model, stop, wvl)

    def lookup_fld_wvl_focus(self, fi, wl=None, fr=0.0):
        if wl is None:
            wvl = self.spectral_region.central_wvl()
        else:
            wvl = self.spectral_region.wavelengths[wl]
        fld = self.field_of_view.fields[fi]
        foc = self.defocus.get_focus(fr)
        return fld, wvl, foc

    def trace_base(self, seq_model, pupil, fld, wvl, eps=1.0e-12):
        vig_pupil = fld.apply_vignetting(pupil)
        fod = self.parax_data.fod
        eprad = fod.enp_radius
        pt1 = np.array([eprad*vig_pupil[0], eprad*vig_pupil[1],
                        fod.obj_dist+fod.enp_dist])
        pt0 = self.obj_coords(fld)
        dir0 = pt1 - pt0
        length = norm(dir0)
        dir0 = dir0/length
        return rt.trace(seq_model, pt0, dir0, wvl, eps)

    def trace_with_opd(self, seq_model, pupil, fld, wvl, foc, eps=1.0e-12):
        """ returns ray and ray_op """
        ray_pkg = self.trace_base(seq_model, pupil, fld, wvl, eps)

        rs_pkg, cr_pkg = self.setup_pupil_coords(seq_model, fld, wvl, foc)
        fld.chief_ray = cr_pkg
        fld.ref_sphere = rs_pkg

        opd_pkg = rt.wave_abr(seq_model, fld, wvl, ray_pkg)
        ray, ray_op, wvl = ray_pkg
        return ray, ray_op, wvl, opd_pkg[0]

    def trace(self, seq_model, pupil, fi, wl=None, eps=1.0e-12):
        """ returns ray and ray_op """
        fld, wvl, foc = self.lookup_fld_wvl_focus(fi, wl, 0.0)
        ray, ray_op, wvl = self.trace_base(seq_model, pupil, fld, wvl, eps)
        return ray, ray_op, wvl

    def trace_fan(self, seq_model, fan_rng, fld, wvl, foc, img_filter=None,
                  eps=1.0e-12):
        start = np.array(fan_rng[0])
        stop = fan_rng[1]
        num = fan_rng[2]
        step = (stop - start)/(num - 1)
        fan = []
        for r in range(num):
            pupil = np.array(start)
            ray_pkg = self.trace_base(seq_model, pupil, fld, wvl, eps)

            if img_filter:
                result = img_filter(pupil, ray_pkg)
                fan.append([pupil, result])
            else:
                fan.append([pupil, ray_pkg])

            start += step
        return fan

    def trace_grid(self, seq_model, grid_rng, fld, wvl, foc, img_filter=None,
                   form='grid', append_if_none=True, eps=1.0e-12):
        start = np.array(grid_rng[0])
        stop = grid_rng[1]
        num = grid_rng[2]
        step = np.array((stop - start)/(num - 1))
        grid = []
        for i in range(num):
            if form == 'list':
                working_grid = grid
            elif form == 'grid':
                grid_row = []
                working_grid = grid_row

            for j in range(num):
                pupil = np.array(start)
                if (pupil[0]**2 + pupil[1]**2) < 1.0:
                    ray_pkg = self.trace_base(seq_model, pupil, fld, wvl, eps)
                    if img_filter:
                        result = img_filter(pupil, ray_pkg)
                        working_grid.append(result)
                    else:
                        working_grid.append([pupil[0], pupil[1], ray_pkg])
                else:  # ray outside pupil
                    if img_filter:
                        result = img_filter(pupil, None)
                        if result is not None or append_if_none:
                            working_grid.append(result)
                    else:
                        if append_if_none:
                            working_grid.append([pupil[0], pupil[1], None])

                start[1] += step[1]
            if form == 'grid':
                grid.append(grid_row)
            start[0] += step[0]
            start[1] = grid_rng[0][1]
        return np.array(grid)

    def trace_boundary_rays_at_field(self, seq_model, fld, wvl):
        rim_rays = []
        for p in self.pupil.pupil_rays:
            ray, op, wvl = self.trace_base(seq_model, p, fld, wvl)
            rim_rays.append([ray, op, wvl])
        return rim_rays

    def trace_ray_list_at_field(self, seq_model, ray_list, fld, wvl, foc):
        rayset = pd.DataFrame(data=np.nan)
        for p in ray_list:
            ray, op, wvl = self.trace_base(seq_model, p, fld, wvl)
            rayset[(fld, wvl, foc, p)] = ray
        return rayset

    def trace_field(self, seq_model, fld, wvl):
        rayset = self.trace_boundary_rays_at_field(seq_model, fld, wvl)
        rdf_list = [rt.ray_df(r[0]) for r in rayset]
        rset = pd.concat(rdf_list, keys=self.pupil.ray_labels,
                         names=['pupil'])
        return rset

    def boundary_rays_df(self, seq_model, pupil_spec, rim_rays):
        """ return a DataFrame containing the boundary ray set """
        rays = []
        for p, r in zip(pupil_spec.pupil_rays, rim_rays):
            ray_df = rt.ray_df(r[0])
            ray_df.index = seq_model.surface_label_list()
            ray_pkg = pd.Series((p, ray_df, *r[1:]),
                                index=['pupil', 'ray', 'op', 'wvl'])
            rays.append(ray_pkg)
        ray_set = pd.DataFrame(rays, index=pupil_spec.ray_labels)
        return ray_set

    def trace_boundary_rays(self, seq_model):
        rayset = []
        fov = self.field_of_view
        wvl = self.spectral_region.central_wvl()
        for fld in fov.fields:
            rim_rays = self.trace_boundary_rays_at_field(seq_model, fld, wvl)
            rayset.append(rim_rays)
        return rayset

    def trace_boundary_rays_df(self, seq_model):
        """
        set up a multi-index that varies over:
            surface, pupil and field. wavelength and focus are constants
            """
        rayset = []
        mi = pd.MultiIndex.from_product([self.field_of_view.index_labels,
                                         self.pupil.ray_labels,
                                         seq_model.surface_label_list()],
                                        names=['field', 'pupil', 'surf'])
        fov = self.field_of_view
        wvl = self.spectral_region.central_wvl()
        for fld in fov.fields:
            rim_rays = self.trace_boundary_rays_at_field(seq_model, fld, wvl)
            rayset.append(rim_rays)
        return rayset

    def obj_coords(self, fld):
        fov = self.field_of_view
        fod = self.parax_data.fod
        if fov.type == 'OBJ_ANG':
            ang_dg = np.array([fld.x, fld.y, 0.0])
            dir_tan = np.tan(np.deg2rad(ang_dg))
            obj_pt = -dir_tan*(fod.obj_dist+fod.enp_dist)
        elif fov.type == 'IMG_HT':
            img_pt = np.array([fld.x, fld.y, 0.0])
            obj_pt = -fod.red*img_pt
        else:
            obj_pt = np.array([fld.x, fld.y, 0.0])
        return obj_pt

    def trace_chief_ray(self, seq_model, fld, wvl, foc):
        fod = self.parax_data.fod

        ray, op, wvl = self.trace_base(seq_model, [0., 0.], fld, wvl)
        cr = rt.RayPkg(ray, op, wvl)

        # cr_exp_pt: E upper bar prime: pupil center for pencils from Q
        # cr_exp_pt, cr_b4_dir, cr_exp_dist
        cr_exp_seg = rt.transfer_to_exit_pupil(seq_model.ifcs[-2],
                                               (cr.ray[-2][0], cr.ray[-2][1]),
                                               fod.exp_dist)
        return cr, cr_exp_seg

    def setup_pupil_coords(self, seq_model, fld, wvl, foc,
                           chief_ray_pkg=None, image_pt=None):
        if chief_ray_pkg is None:
            chief_ray_pkg = self.trace_chief_ray(seq_model, fld, wvl, foc)
        elif chief_ray_pkg[2] != wvl:
            chief_ray_pkg = self.trace_chief_ray(seq_model, fld, wvl, foc)

        cr, cr_exp_seg = chief_ray_pkg

        if image_pt is None:
            image_pt = cr.ray[-1][0]

        # cr_exp_pt: E upper bar prime: pupil center for pencils from Q
        # cr_exp_pt, cr_b4_dir, cr_dst
        cr_exp_pt = cr_exp_seg[0]
        cr_exp_dist = cr_exp_seg[2]

        img_dist = seq_model.gaps[-1].thi
        img_pt = np.array(image_pt)
        img_pt[2] += img_dist

        # R' radius of reference sphere for O'
        ref_sphere_vec = img_pt - cr_exp_pt
        ref_sphere_radius = np.linalg.norm(ref_sphere_vec)
        ref_dir = normalize(ref_sphere_vec)

        ref_sphere = (image_pt, cr_exp_pt, cr_exp_dist,
                      ref_dir, ref_sphere_radius)

        z_dir = seq_model.z_dir[-1]
        n_obj = seq_model.rndx[wvl].iloc[0]
        n_img = seq_model.rndx[wvl].iloc[-1]
        ref_sphere_pkg = (ref_sphere, self.parax_data, n_obj, n_img, z_dir)

        return ref_sphere_pkg, chief_ray_pkg

    def setup_canonical_coords(self, seq_model, fld, wvl, image_pt=None):
        fod = self.parax_data.fod

        if fld.chief_ray is None:
            ray, op, wvl = self.trace_base(seq_model, [0., 0.], fld, wvl)
            fld.chief_ray = rt.RayPkg(ray, op, wvl)
        cr = fld.chief_ray

        if image_pt is None:
            image_pt = cr.ray[-1][0]

        # cr_exp_pt: E upper bar prime: pupil center for pencils from Q
        # cr_exp_pt, cr_b4_dir, cr_dst
        cr_exp_seg = rt.transfer_to_exit_pupil(seq_model.ifcs[-2],
                                               (cr.ray[-2][0], cr.ray[-2][1]),
                                               fod.exp_dist)
        cr_exp_pt = cr_exp_seg[0]
        cr_exp_dist = cr_exp_seg[2]

        img_dist = seq_model.gaps[-1].thi
        img_pt = np.array(image_pt)
        img_pt[2] += img_dist

        # R' radius of reference sphere for O'
        ref_sphere_vec = img_pt - cr_exp_pt
        ref_sphere_radius = np.linalg.norm(ref_sphere_vec)
        ref_dir = normalize(ref_sphere_vec)

        ref_sphere = (image_pt, cr_exp_pt, cr_exp_dist,
                      ref_dir, ref_sphere_radius)

        z_dir = seq_model.z_dir[-1]
        n_obj = seq_model.rndx[wvl].iloc[0]
        n_img = seq_model.rndx[wvl].iloc[-1]
        ref_sphere_pkg = (ref_sphere, self.parax_data, n_obj, n_img, z_dir)
        fld.ref_sphere = ref_sphere_pkg
        return ref_sphere_pkg, cr


class WvlSpec:
    """ Class defining a spectral region

    A spectral region is a list of wavelengths (in nm) and corresponding
    weights. A reference wavelength index defines the "center" of the
    spectral region.

    """
    def __init__(self, wlwts=[(550., 1.)], ref_wl=0):
        self.set_from_list(wlwts)
        self.reference_wvl = ref_wl
        self.coating_wvl = 550.0

    def central_wvl(self):
        return self.wavelengths[self.reference_wvl]

    def set_from_list(self, wlwts):
        self.wavelengths = []
        self.spectral_wts = []
        for wlwt in wlwts:
            self.wavelengths.append(wlwt[0])
            self.spectral_wts.append(wlwt[1])
        self.calc_colors()

    def add(self, wl, wt):
        self.wavelengths.append(wl)
        self.spectral_wts.append(wt)
        self.spectrum.sort(key=lambda w: w[0], reverse=True)

    def calc_colors(self):
        self.render_colors = []
        num_wvls = len(self.wavelengths)
        if num_wvls == 1:
            self.render_colors.append('black')
        elif num_wvls == 2:
            self.render_colors.append('red')
            self.render_colors.append('blue')
        elif num_wvls == 3:
            self.render_colors.append('red')
            self.render_colors.append('green')
            self.render_colors.append('blue')
        else:
            for w in self.wavelengths:
                print("calc_colors", w)
                rgb = srgb.wvl_to_rgb(w)
                print("rgb", rgb)
                self.render_colors.append(rgb)


class PupilSpec:
    types = ('EPD', 'NA', 'NAO', 'FNO')
    default_pupil_rays = [[0., 0.], [1., 0.], [-1., 0.], [0., 1.], [0., -1.]]
    default_ray_labels = ['00', '+X', '-X', '+Y', '-Y']

    def __init__(self, type='EPD', value=1.0):
        self.type = type
        self.value = value
        self.pupil_rays = PupilSpec.default_pupil_rays
        self.ray_labels = PupilSpec.default_ray_labels

    def set_from_list(self, ppl_spec):
        self.type = ppl_spec[0]
        self.value = ppl_spec[1]

    def update_model(self, seq_model):
        if not hasattr(self, 'pupil_rays'):
            self.pupil_rays = PupilSpec.default_pupil_rays
            self.ray_labels = PupilSpec.default_ray_labels


class FieldSpec:
    types = ('OBJ_ANG', 'OBJ_HT', 'IMG_HT')

    def __init__(self, type='OBJ_ANG', flds=[0.], wide_angle=False):
        self.type = type
        self.fields = [Field() for f in range(len(flds))]
        for i, f in enumerate(self.fields):
            f.y = flds[i]
        self.wide_angle = wide_angle

    def set_from_list(self, flds):
        self.fields = [Field() for f in range(len(flds))]
        for i, f in enumerate(self.fields):
            f.y = flds[i]

    def update_model(self, seq_model):
        for f in self.fields:
            f.update()

        max_field, fi = self.max_field()
        field_norm = 1.0 if max_field == 0 else 1.0/max_field
        self.index_labels = [str(field_norm*f.y)+'F' for f in self.fields]
        self.index_labels[0] = 'axis'
        if len(self.index_labels) > 1:
            self.index_labels[-1] = 'edge'
        return self

    def update_fields_cv_input(self, tla, dlist):
        if tla == 'XOB' or tla == 'YOB':
            self.type = 'OBJ_HT'
        elif tla == 'XAN' or tla == 'YAN':
            self.type = 'OBJ_ANG'
        elif tla == 'XIM' or tla == 'YIM':
            self.type = 'IMG_HT'

        if len(self.fields) != len(dlist):
            self.fields = [Field() for f in range(len(dlist))]

        if tla[0] == 'V':
            attr = tla.lower()
        elif tla[0] == 'X' or tla[0] == 'Y':
            attr = tla[0].lower()
        elif tla == 'WTF':
            attr = 'wt'

        for i, f in enumerate(self.fields):
            f.__setattr__(attr, dlist[i])

    def max_field(self):
        max_fld = None
        max_fld_sqrd = 0.0
        for i, f in enumerate(self.fields):
            fld_sqrd = f.x*f.x + f.y*f.y
            if fld_sqrd > max_fld_sqrd:
                max_fld_sqrd = fld_sqrd
                max_fld = i
        return math.sqrt(max_fld_sqrd), max_fld


class Field:
    def __init__(self, x=0., y=0., wt=1.):
        self.x = x
        self.y = y
        self.vux = 0.0
        self.vuy = 0.0
        self.vlx = 0.0
        self.vly = 0.0
        self.wt = wt
        self.chief_ray = None
        self.ref_sphere = None

    def __json_encode__(self):
        attrs = dict(vars(self))
        del attrs['chief_ray']
        del attrs['ref_sphere']
        return attrs

    def __str__(self):
        return "{}, {}".format(self.x, self.y)

    def update(self):
        self.chief_ray = None
        self.ref_sphere = None

    def apply_vignetting(self, pupil):
        vig_pupil = pupil[:]
        if pupil[0] < 0.0:
            if self.vlx != 0.0:
                vig_pupil[0] *= (1.0 - self.vlx)
        else:
            if self.vux != 0.0:
                vig_pupil[0] *= (1.0 - self.vux)
        if pupil[1] < 0.0:
            if self.vly != 0.0:
                vig_pupil[1] *= (1.0 - self.vly)
        else:
            if self.vuy != 0.0:
                vig_pupil[1] *= (1.0 - self.vuy)
        return vig_pupil


class FocusRange:
    def __init__(self, defocus, infocus=0.0):
        self.infocus = infocus
        self.defocus = defocus

    def update(self):
        self.chief_ray = None
        self.ref_sphere = None

    def get_focus(self, fr):
        """ return focus position for input focus range parameter

        fr, focus range parameter, -1.0 to 1.0
        """
        return self.infocus + fr*self.defocus