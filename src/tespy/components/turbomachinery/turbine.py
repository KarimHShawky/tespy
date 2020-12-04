# -*- coding: utf-8

"""Module of class Turbine.


This file is part of project TESPy (github.com/oemof/tespy). It's copyrighted
by the contributors recorded in the version control history of the file,
available from its original location tespy/components/turbomachinery/turbine.py

SPDX-License-Identifier: MIT
"""

import logging

import numpy as np

from tespy.components.component import Component
from tespy.components.turbomachinery.turbomachine import Turbomachine
from tespy.tools.data_containers import ComponentCharacteristics as dc_cc
from tespy.tools.data_containers import ComponentProperties as dc_cp
from tespy.tools.data_containers import DataContainerSimple as dc_simple
from tespy.tools.fluid_properties import isentropic
from tespy.tools.fluid_properties import v_mix_ph
from tespy.tools.global_vars import err


class Turbine(Turbomachine):
    r"""
    Class for gas or steam turbines.

    Equations

        **mandatory equations**

        - :py:meth:`tespy.components.component.Component.fluid_func`
        - :py:meth:`tespy.components.component.Component.mass_flow_func`

        **optional equations**

        .. math::

            0 = \dot{m}_{in} \cdot \left( h_{out} - h_{in} \right) - P\\
            0 = pr \cdot p_{in} - p_{out}

        **additional equations**

        - :py:meth:`tespy.components.turbomachinery.turbine.Turbine.additional_equations`

    Inlets/Outlets

        - in1
        - out1

    Image

        .. image:: _images/Turbine.svg
           :scale: 100 %
           :alt: alternative text
           :align: center

    Parameters
    ----------
    label : str
        The label of the component.

    design : list
        List containing design parameters (stated as String).

    offdesign : list
        List containing offdesign parameters (stated as String).

    design_path: str
        Path to the components design case.

    local_offdesign : boolean
        Treat this component in offdesign mode in a design calculation.

    local_design : boolean
        Treat this component in design mode in an offdesign calculation.

    char_warnings: boolean
        Ignore warnings on default characteristics usage for this component.

    printout: boolean
        Include this component in the network's results printout.

    P : float, tespy.tools.data_containers.ComponentProperties
        Power, :math:`P/\text{W}`

    eta_s : float, tespy.tools.data_containers.ComponentProperties
        Isentropic efficiency, :math:`\eta_s/1`

    pr : float, tespy.tools.data_containers.ComponentProperties
        Outlet to inlet pressure ratio, :math:`pr/1`

    eta_s_char : tespy.tools.characteristics.CharLine, tespy.tools.data_containers.ComponentCharacteristics
        Characteristic curve for isentropic efficiency, provide CharLine as
        function :code:`func`.

    cone : tespy.tools.data_containers.DataContainerSimple
        Apply Stodola's cone law.

    Example
    -------
    A steam turbine expands 10 kg/s of superheated steam at 550 °C and 110 bar
    to 0,5 bar at the outlet. For example, it is possible to calulate the power
    output and vapour content at the outlet for a given isentropic efficiency.

    >>> from tespy.components import Sink, Source, Turbine
    >>> from tespy.connections import Connection
    >>> from tespy.networks import Network
    >>> from tespy.tools import ComponentCharacteristics as dc_cc
    >>> import shutil
    >>> fluid_list = ['water']
    >>> nw = Network(fluids=fluid_list, p_unit='bar', T_unit='C',
    ... h_unit='kJ / kg', iterinfo=False)
    >>> si = Sink('sink')
    >>> so = Source('source')
    >>> t = Turbine('turbine')
    >>> t.component()
    'turbine'
    >>> inc = Connection(so, 'out1', t, 'in1')
    >>> outg = Connection(t, 'out1', si, 'in1')
    >>> nw.add_conns(inc, outg)

    In design conditions the isentropic efficiency is specified. For offdesign
    a characteristic function will be applied, together with Stodola's cone
    law coupling the turbine mass flow to inlet pressure.

    >>> t.set_attr(eta_s=0.9, design=['eta_s'],
    ... offdesign=['eta_s_char', 'cone'])
    >>> inc.set_attr(fluid={'water': 1}, m=10, T=550, p=110, design=['p'])
    >>> outg.set_attr(p=0.5)
    >>> nw.solve('design')
    >>> nw.save('tmp')
    >>> round(t.P.val, 0)
    -10452574.0
    >>> round(outg.x.val, 3)
    0.914
    >>> inc.set_attr(m=8)
    >>> nw.solve('offdesign', design_path='tmp')
    >>> round(t.eta_s.val, 3)
    0.898
    >>> round(inc.p.val, 1)
    88.6
    >>> shutil.rmtree('./tmp', ignore_errors=True)
    """

    @staticmethod
    def component():
        return 'turbine'

    @staticmethod
    def attr():
        return {
            'P': dc_cp(max_val=0),
            'eta_s': dc_cp(min_val=0, max_val=1),
            'eta_s_char': dc_cc(param='m'),
            'pr': dc_cp(min_val=0, max_val=1),
            'cone': dc_simple(),
            'Sirr': dc_simple()
        }

    def comp_init(self, nw):

        Component.comp_init(self, nw)

        if ((nw.mode == 'offdesign' or self.local_offdesign is True) and
                self.local_design is False):
            self.dh_s_ref = (
                isentropic(
                    self.inl[0].to_flow_design(),
                    self.outl[0].to_flow_design(),
                    T0=self.inl[0].T.val_SI) - self.inl[0].h.design)

        # number of mandatroy equations for
        # fluid balance: num_fl
        # mass flow: 1
        self.num_eq = self.num_nw_fluids + 1
        for var in [self.P, self.pr, self.eta_s, self.eta_s_char, self.cone]:
            if var.is_set is True:
                self.num_eq += 1

        self.jacobian = np.zeros((
            self.num_eq,
            self.num_i + self.num_o + self.num_vars,
            self.num_nw_vars))

        self.residual = np.zeros(self.num_eq)
        pos = self.num_nw_fluids
        self.jacobian[0:pos] = self.fluid_deriv()
        self.jacobian[pos:pos + 1] = self.mass_flow_deriv()

    def additional_equations(self, k):
        r"""
        Calculate results of additional equations.

        Equations

            **optional equations**

            - :py:meth:`tespy.components.turbomachinery.turbine.Turbine.eta_s_func`
            - :py:meth:`tespy.components.turbomachinery.turbine.Turbine.eta_s_char_func`
            - :py:meth:`tespy.components.turbomachinery.turbine.Turbine.cone_func`
        """
        ######################################################################
        # eqations for specified isentropic efficiency
        if self.eta_s.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.eta_s_func()
            k += 1

        ######################################################################
        # derivatives for specified isentropic efficiency characteristics
        if self.eta_s_char.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.eta_s_char_func()
            k += 1

        ######################################################################
        # equation for specified cone law
        if self.cone.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.cone_func()
            k += 1

    def additional_derivatives(self, increment_filter, k):
        r"""Calculate partial derivatives for given additional equations."""
        ######################################################################
        # derivatives for specified isentropic efficiency
        if self.eta_s.is_set:
            f = self.eta_s_func
            if not increment_filter[0, 1]:
                self.jacobian[k, 0, 1] = self.numeric_deriv(f, 'p', 0)
            if not increment_filter[1, 1]:
                self.jacobian[k, 1, 1] = self.numeric_deriv(f, 'p', 1)
            if not increment_filter[0, 2]:
                self.jacobian[k, 0, 2] = self.numeric_deriv(f, 'h', 0)
            self.jacobian[k, 1, 2] = -1
            k += 1

        ######################################################################
        # derivatives for specified isentropic efficiency characteristics
        if self.eta_s_char.is_set:
            f = self.eta_s_char_func
            if not increment_filter[0, 0]:
                self.jacobian[k, 0, 0] = self.numeric_deriv(f, 'm', 0)
            if not increment_filter[0, 1]:
                self.jacobian[k, 0, 1] = self.numeric_deriv(f, 'p', 0)
            if not increment_filter[0, 2]:
                self.jacobian[k, 0, 2] = self.numeric_deriv(f, 'h', 0)
            if not increment_filter[1, 1]:
                self.jacobian[k, 1, 1] = self.numeric_deriv(f, 'p', 1)
            if not increment_filter[1, 2]:
                self.jacobian[k, 1, 2] = self.numeric_deriv(f, 'h', 1)
            k += 1

        ######################################################################
        # derivatives for specified cone law
        if self.cone.is_set:
            f = self.cone_func
            self.jacobian[k, 0, 0] = -1
            if not increment_filter[0, 1]:
                self.jacobian[k, 0, 1] = self.numeric_deriv(f, 'p', 0)
            if not increment_filter[0, 2]:
                self.jacobian[k, 0, 2] = self.numeric_deriv(f, 'h', 0)
            if not increment_filter[1, 2]:
                self.jacobian[k, 1, 2] = self.numeric_deriv(f, 'p', 1)
            k += 1

        return

    def eta_s_func(self):
        r"""
        Equation for given isentropic efficiency of a turbine.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                0 = -\left( h_{out} - h_{in} \right) +
                \left( h_{out,s} - h_{in} \right) \cdot \eta_{s,e}
        """
        return (
            -(self.outl[0].h.val_SI - self.inl[0].h.val_SI) + (
                isentropic(
                    self.inl[0].to_flow(), self.outl[0].to_flow(),
                    T0=self.inl[0].T.val_SI) -
                self.inl[0].h.val_SI) * self.eta_s.val)

    def cone_func(self):
        r"""
        Equation for stodolas cone law.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                0 = \frac{\dot{m}_{in,ref} \cdot p_{in}}{p_{in,ref}} \cdot
                \sqrt{\frac{p_{in,ref} \cdot v_{in}}{p_{in} \cdot v_{in,ref}}}
                \cdot \sqrt{\frac{1 - \left(\frac{p_{out}}{p_{in}} \right)^{2}}
                {1 - \left(\frac{p_{out,ref}}{p_{in,ref}} \right)^{2}}} -
                \dot{m}_{in}
        """
        # actual values
        i = self.inl[0].to_flow()
        o = self.outl[0].to_flow()
        # design values
        i_d = self.inl[0].to_flow_design()
        o_d = self.outl[0].to_flow_design()

        v_i = v_mix_ph(i, T0=self.inl[0].T.val_SI)

        n = 1
        return (- i[0] + i_d[0] * i[1] / i_d[1] *
                np.sqrt(i_d[1] * v_mix_ph(i_d) / (i[1] * v_i)) *
                np.sqrt(abs((1 - (o[1] / i[1]) ** ((n + 1) / n)) /
                            (1 - (o_d[1] / i_d[1]) ** ((n + 1) / n)))))

    def eta_s_char_func(self):
        r"""
        Equation for given isentropic efficiency characteristic.

        Returns
        -------
        res : ndarray
            Residual value of equation.

            .. math::

                0 = - \left( h_{out} - h_{in} \right) + \eta_{s,e,0} \cdot
                f\left( expr \right) \cdot \Delta h_{s}
        """
        # actual values
        i = self.inl[0].to_flow()
        o = self.outl[0].to_flow()
        # design values
        i_d = self.inl[0].to_flow_design()
        o_d = self.outl[0].to_flow_design()

        if self.eta_s_char.param == 'dh_s':
            expr = np.sqrt(self.dh_s_ref / (
                isentropic(i, o, T0=self.inl[0].T.val_SI) - i[2]))
        elif self.eta_s_char.param == 'm':
            expr = i[0] / i_d[0]
        elif self.eta_s_char.param == 'v':
            v_i = v_mix_ph(i, T0=self.inl[0].T.val_SI)
            expr = i[0] * v_i / (i_d[0] * v_mix_ph(i_d))
        elif self.eta_s_char.param == 'pr':
            expr = (o[1] * i_d[1]) / (i[1] * o_d[1])
        else:
            msg = ('Please choose the parameter, you want to link the '
                   'isentropic efficiency to.')
            logging.error(msg)
            raise ValueError(msg)

        return (
            -(o[2] - i[2]) + self.eta_s.design *
            self.eta_s_char.func.evaluate(expr) * (
                isentropic(i, o, T0=self.inl[0].T.val_SI) - i[2]))

    def convergence_check(self):
        r"""
        Perform a convergence check.

        Note
        ----
        Manipulate enthalpies/pressure at inlet and outlet if not specified by
        user to match physically feasible constraints.
        """
        i, o = self.inl, self.outl

        if i[0].good_starting_values is False:
            if i[0].p.val_SI <= 1e5 and not i[0].p.val_set:
                i[0].p.val_SI = 1e5

            if i[0].h.val_SI < 10e5 and not i[0].h.val_set:
                i[0].h.val_SI = 10e5

            if o[0].h.val_SI < 5e5 and not o[0].h.val_set:
                o[0].h.val_SI = 5e5

        if i[0].h.val_SI <= o[0].h.val_SI and not o[0].h.val_set:
            o[0].h.val_SI = i[0].h.val_SI * 0.9

        if i[0].p.val_SI <= o[0].p.val_SI and not o[0].p.val_set:
            o[0].p.val_SI = i[0].p.val_SI * 0.9

    @staticmethod
    def initialise_Source(c, key):
        r"""
        Return a starting value for pressure and enthalpy at outlet.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                5 \cdot 10^4 & \text{key = 'p'}\\
                1.5 \cdot 10^6 & \text{key = 'h'}
                \end{cases}
        """
        if key == 'p':
            return 0.5e5
        elif key == 'h':
            return 1.5e6

    @staticmethod
    def initialise_target(c, key):
        r"""
        Return a starting value for pressure and enthalpy at inlet.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                2.5 \cdot 10^6 & \text{key = 'p'}\\
                2 \cdot 10^6 & \text{key = 'h'}
                \end{cases}
        """
        if key == 'p':
            return 2.5e6
        elif key == 'h':
            return 2e6

    def calc_parameters(self):
        r"""Postprocessing parameter calculation."""
        Turbomachine.calc_parameters(self)

        self.eta_s.val = (
            (self.outl[0].h.val_SI - self.inl[0].h.val_SI) / (
                isentropic(
                    self.inl[0].to_flow(), self.outl[0].to_flow(),
                    T0=self.inl[0].T.val_SI) - self.inl[0].h.val_SI))

        if self.eta_s_char.is_set:
            # get bound errors for isentropic efficiency characteristics
            i = self.inl[0].to_flow()
            o = self.outl[0].to_flow()
            i_d = self.inl[0].to_flow_design()
            o_d = self.outl[0].to_flow_design()

            if self.eta_s_char.param == 'dh_s':
                expr = np.sqrt(self.dh_s_ref / (isentropic(
                    i, o, T0=self.inl[0].T.val_SI) - i[2]))
            elif self.eta_s_char.param == 'm':
                expr = i[0] / i_d[0]
            elif self.eta_s_char.param == 'v':
                v_i = v_mix_ph(i, T0=self.inl[0].T.val_SI)
                expr = i[0] * v_i / (i_d[0] * v_mix_ph(i_d))
            elif self.eta_s_char.param == 'pr':
                expr = (o[1] * i_d[1]) / (i[1] * o_d[1])

            self.eta_s_char.func.get_bound_errors(expr, self.label)

        self.check_parameter_bounds()
