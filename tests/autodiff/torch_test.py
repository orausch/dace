from functools import reduce

import torch
import numpy as np

import dace
import dace.graph.nodes as nd
from dace.autodiff import BackwardPassGenerator
from dace.libraries.blas import ExpandGemmPure
from dace.transformation import optimizer

all_tests = []


def correctness_test(func):
    def check_correctness():
        runner, pytorch_func, inputs = func()
        sdfg_dict = {name: arr.copy() for name, arr in inputs.items()}
        torch_dict = {
            name: torch.tensor(arr.copy(), requires_grad=True)
            for name, arr in inputs.items()
        }

        sdfg_results = runner.run(**sdfg_dict)
        torch_results = pytorch_func(**torch_dict)

        for k, v in torch_results.items():
            v = v.detach().numpy()
            diff = np.linalg.norm(sdfg_results[k] - v) / reduce(
                lambda x, y: x * y, v.shape)
            assert diff < 1e-5

    all_tests.append(check_correctness)
    return check_correctness


class SDFGBackwardRunner:
    def __init__(self, sdfg, target):
        sdfg.expand_library_nodes()
        sdfg.apply_strict_transformations()
        self.sdfg = sdfg
        self.target = target
        state = sdfg.nodes()[0]
        required_grads = set(node.data for node in state.source_nodes())

        matches = [
            node for node in state.nodes()
            if type(node) is nd.AccessNode and node.data == target
        ]
        if len(matches) > 1:
            raise ValueError(
                "Found more than one accessnode with data {}".format(target))

        BackwardPassGenerator(sdfg, state, required_grads,
                              matches[0]).backward()
        self.sdfg.apply_strict_transformations()

    def run(self, **inputs):
        # zero out all arrays
        intermediate_arrs = {
            name: np.zeros(arr.shape, dtype=getattr(np, arr.dtype.to_string()))
            for name, arr in self.sdfg.arrays.items()
            if name != self.target + "_grad" if not name.startswith("__")
            if name not in inputs if not arr.transient
        }
        inputs.update(intermediate_arrs)
        inputs[self.target + "_grad"] = np.ones(
            (1, ),
            dtype=getattr(np, self.sdfg.arrays[self.target].dtype.to_string()))
        self.sdfg.save("out.sdfg")
        self.sdfg(**inputs)

        results = {
            name: arr
            for name, arr in inputs.items()
            if name.endswith("_grad") and name != self.target + "_grad"
        }
        return results


@correctness_test
def test_gemm():
    def torch_gemm(*, X, Y):
        Z = X @ Y
        S = Z.sum()
        S.backward()
        return dict(X_grad=X.grad, Y_grad=Y.grad)

    @dace.program
    def dace_gemm(X: dace.float32[5, 4], Y: dace.float32[4, 3],
                  Z: dace.float32[5, 3], S: dace.float32[1]):

        Z[:] = X @ Y

        @dace.map(_[0:5, 0:3])
        def summap(i, j):
            s >> S(1, lambda x, y: x + y)[0]
            z << Z[i, j]
            s = z

    sdfg = dace_gemm.to_sdfg()

    return SDFGBackwardRunner(sdfg, "S"), torch_gemm, dict(
        X=np.random.rand(5, 4).astype(np.float32),
        Y=np.random.rand(4, 3).astype(np.float32))


@correctness_test
def test_sum():
    def torch_sum(*, X, Y):
        Z = X + Y
        Z = Z * Z
        S = Z.sum()
        S.backward()
        return dict(X_grad=X.grad, Y_grad=Y.grad)

    @dace.program
    def dace_sum(X: dace.float32[3, 3], Y: dace.float32[3, 3],
                 Z: dace.float32[3, 3], S: dace.float32[1]):

        Z[:] = X + Y

        @dace.map(_[0:3, 0:3])
        def summap(i, j):
            s >> S(1, lambda x, y: x + y)[0]
            z << Z[i, j]
            s = z * z

    sdfg = dace_sum.to_sdfg()
    state = sdfg.nodes()[0]

    return SDFGBackwardRunner(sdfg, "S"), torch_sum, dict(
        X=np.random.rand(3, 3).astype(np.float32),
        Y=np.random.rand(3, 3).astype(np.float32))


@correctness_test
def test_complex_tasklet():
    def torch_sum(*, X, Y):
        Z = X + Y
        Z = Z * Z
        S = Z.sum()
        S.backward()
        return dict(X_grad=X.grad, Y_grad=Y.grad)

    @dace.program
    def dace_sum(X: dace.float32[3, 3], Y: dace.float32[3, 3],
                 Z: dace.float32[3, 3], S: dace.float32[1]):

        Z[:] = X + Y

        @dace.map(_[0:3, 0:3])
        def summap(i, j):
            s >> S(1, lambda x, y: x + y)[0]
            z << Z[i, j]

            z1 = z + 1
            log(3)  # random expr
            z2 = z - 1 * (2 / 2)
            # hello world 1, 2, 3
            s = z1 * z2

    sdfg = dace_sum.to_sdfg()
    state = sdfg.nodes()[0]

    return SDFGBackwardRunner(sdfg, "S"), torch_sum, dict(
        X=np.random.rand(3, 3).astype(np.float32),
        Y=np.random.rand(3, 3).astype(np.float32))


@correctness_test
def test_tasklets_only():
    def torch_func(*, A, B):
        tmp_a = torch.sqrt(A)
        tmp_b = torch.log(B + 1)

        C = tmp_a * tmp_b

        C.backward()
        return dict(A_grad=A.grad, B_grad=B.grad)

    @dace.program
    def dace_func(A: dace.float32[1], B: dace.float32[1], C: dace.float32[1]):
        tmp_a = dace.define_local_scalar(dace.float32)
        tmp_b = dace.define_local_scalar(dace.float32)

        with dace.tasklet:
            a << A[0]
            a_out >> tmp_a

            a_out = sqrt(a)

        with dace.tasklet:
            a << B[0]
            a_out >> tmp_b

            a_out = log(a + 1)

        with dace.tasklet:
            a << tmp_a
            b << tmp_b
            c >> C[0]
            c = a * b

    sdfg = dace_func.to_sdfg()

    return SDFGBackwardRunner(sdfg, "C"), torch_func, dict(
        A=np.random.rand(1).astype(np.float32),
        B=np.random.rand(1).astype(np.float32))


@correctness_test
def test_add_mmul_transpose_log():
    def torch_func(*, X, Y, W):

        Xt = X.T
        YW = W * Y
        Z = Xt @ YW
        Zl = torch.log(Z)

        S = Zl.sum()
        S.backward()
        return dict(X_grad=X.grad, Y_grad=Y.grad)

    @dace.program
    def dace_func(X: dace.float32[4, 5], Y: dace.float32[4, 3],
                  W: dace.float32[4, 3], Z: dace.float32[5, 3],
                  S: dace.float32[1]):

        Xt[:] = np.transpose(X)
        YW[:] = W * Y
        Z[:] = Xt @ YW

        @dace.map(_[0:5, 0:3])
        def summap(i, j):
            s >> S(1, lambda x, y: x + y)[0]
            z << Z[i, j]
            s = log(z)

    sdfg = dace_func.to_sdfg()

    return SDFGBackwardRunner(sdfg, "S"), torch_func, dict(
        X=np.random.rand(4, 5).astype(np.float32),
        W=np.random.rand(4, 3).astype(np.float32),
        Y=np.random.rand(4, 3).astype(np.float32))


def test_all():
    for test in all_tests:
        test()


if __name__ == "__main__":
    test_all()