# pylint: disable=C0103
import theano
import numpy as np
import theano.tensor as tt
from theano.tensor.nlinalg import matrix_inverse
from theano.tensor.slinalg import solve
from theano.tensor.nlinalg import det
from kusanagi import utils


def linear_loss(mx, Sx, target, Q, absolute=True, *args, **kwargs):
    '''
        Linear penalty function c(x) = Q.dot(|x-target|)
    '''
    if Sx is None:
        # deterministic case
        if mx.ndim == 1:
            mx = mx[None, :]
        delta = mx-target
        if absolute:
            delta = tt.abs(delta)
        cost = (delta).dot(Q)
        return cost
    else:
        # stochastic case (moment matching)
        delta = mx-target
        if absolute:
            delta = tt.abs(delta)
        SxQ = Sx.dot(Q)
        m_cost = Q.T.dot(delta)
        s_cost = Q.T.dot(SxQ)
        return m_cost, s_cost


def quadratic_loss(mx, Sx, target, Q, *args, **kwargs):
    '''
        Quadratic penalty function c(x) = (||x-target||_Q)^2
    '''
    if Sx is None:
        # deterministic case
        if mx.ndim == 1:
            mx = mx[None, :]
        delta = mx-target
        deltaQ = delta.dot(Q)
        cost = tt.batched_dot(deltaQ, delta)
        return cost
    else:
        # stochastic case (moment matching)
        delta = mx-target
        deltaQ = delta.T.dot(Q)
        SxQ = Sx.dot(Q)
        m_cost = tt.sum(Sx*Q) + deltaQ.dot(delta)
        s_cost = 2*tt.sum(SxQ.dot(SxQ)) + 4*deltaQ.dot(Sx).dot(deltaQ.T)
        return m_cost, s_cost


def quadratic_saturating_loss(mx, Sx, target, Q, *args, **kwargs):
    '''
        Squashing loss penalty function
        c(x) = ( 1 - e^(-0.5*quadratic_loss(x, target)) )
    '''
    if Sx is None:
        if mx.ndim == 1:
            mx = mx[None, :]
        delta = mx - target[None, :]
        deltaQ = delta.dot(Q)
        cost = 1.0 - tt.exp(-0.5*tt.batched_dot(deltaQ, delta))
        return cost
    else:
        # stochastic case (moment matching)
        delta = mx - target
        SxQ = Sx.dot(Q)
        EyeM = tt.eye(mx.shape[0])
        IpSxQ = EyeM + SxQ
        Ip2SxQ = EyeM + 2*SxQ
        S1 = tt.dot(Q, matrix_inverse(IpSxQ))
        S2 = tt.dot(Q, matrix_inverse(Ip2SxQ))
        # S1 = solve(IpSxQ.T, Q.T).T
        # S2 = solve(Ip2SxQ.T, Q.T).T
        # mean
        m_cost = -tt.exp(-0.5*delta.dot(S1).dot(delta))/tt.sqrt(det(IpSxQ))
        # var
        s_cost = tt.exp(
            -delta.dot(S2).dot(delta))/tt.sqrt(det(Ip2SxQ)) - m_cost**2

        return 1.0 + m_cost, s_cost


def convert_angle_dimensions(mx, Sx, angle_dims=[]):
    if Sx is None:
        flatten = False
        if mx.ndim == 1:
            flatten = True
            mx = mx[None, :]
        mxa = utils.gTrig(mx, angle_dims)
        if flatten:
            # since we are dealing with one input vector at a time
            mxa = mxa.flatten()
        Sxa = None
    else:
        # angle dimensions are removed, and their complex
        # representation is appended
        mxa, Sxa = utils.gTrig2(mx, Sx, angle_dims)[:2]

    return mxa, Sxa


def distance_based_cost(mx, Sx, target, Q,
                        cw=[1], expl=None, angle_dims=[],
                        loss_func=quadratic_saturating_loss,
                        *args, **kwargs):
    '''
        Loss function that depends on a quadratic function of state
    '''
    if isinstance(target, list) or isinstance(target, tuple):
        target = np.array(target)

    # convert angle dimensions
    if angle_dims:
        if isinstance(target, np.ndarray):
            target = utils.gTrig_np(target, angle_dims).flatten()
        else:
            target = utils.gTrig(target, angle_dims).flatten()
        mx, Sx = convert_angle_dimensions(mx, Sx, angle_dims)

    Q = Q.astype(theano.config.floatX)
    target = target.astype(theano.config.floatX)

    if not isinstance(cw, list):
        cw = [cw]
    if Sx is None:
        # deterministic case
        cost = []
        # total cost is the sum of costs with different widths
        # TODO this can be vecotrized
        for c in cw:
            cost_c = loss_func(mx, None, target, Q/c**2, *args, **kwargs)
            cost.append(cost_c)
        return sum(cost)/len(cw)
    else:
        M_cost = []
        S_cost = []
        # total cost is the sum of costs with different widths
        # TODO this can be vectorized
        for c in cw:
            m_cost, s_cost = loss_func(mx, Sx, target, Q/c**2, *args, **kwargs)
            # add UCB  exploration term
            if expl is not None and expl != 0.0:
                m_cost += expl*tt.sqrt(s_cost)
            M_cost.append(m_cost)
            S_cost.append(s_cost)
        return sum(M_cost)/len(cw), sum(S_cost)/(len(cw)**2)


def build_loss_func(loss, uncertain_inputs=False, name='loss_fn',
                    *args, **kwargs):
    '''
        Utility function to compiling a theano graph corresponding to a loss
        function
    '''
    mx = tt.vector('mx') if uncertain_inputs else tt.matrix('mx')
    Sx = tt.matrix('Sx') if uncertain_inputs else None
    inputs = [mx, Sx] if uncertain_inputs else [mx]
    # add extra input variables
    inputs += [a for a in args
               if type(a) is theano.tensor.TensorVariable
               and len(a.get_parents()) == 0]
    inputs += [k for k in kwargs.values()
               if type(k) is theano.tensor.TensorVariable
               and len(k.get_parents()) == 0]
    outputs = loss(mx, Sx, *args, **kwargs)
    return theano.function(inputs, outputs, name=name,
                           allow_input_downcast=True)


def build_distance_based_cost(uncertain_inputs=False, name='loss_fn',
                              *args, **kwargs):
    '''
        Utility function to compiling a theano graph corresponding to a loss
        function
    '''
    mx = tt.vector('mx') if uncertain_inputs else tt.matrix('mx')
    Sx = tt.matrix('Sx') if uncertain_inputs else None
    Q = kwargs.pop('Q', tt.matrix('Q'))
    target = kwargs.pop('target', tt.vector('target'))
    angi = kwargs.pop('angle_dims', [])
    inputs = [mx, Sx] if uncertain_inputs else [mx]
    if type(target) is tt.TensorVariable and len(target.get_parents()) == 0:
        inputs += [target]
    if type(Q) is tt.TensorVariable and len(Q.get_parents()) == 0:
        inputs += [Q]
    if type(angi) is tt.TensorVariable and len(angi.get_parents()) == 0:
        inputs += [angi]
    outputs = distance_based_cost(mx, Sx, target=target, Q=Q, angle_dims=angi,
                                  *args, **kwargs)
    return theano.function(inputs, outputs, name=name,
                           allow_input_downcast=True)
