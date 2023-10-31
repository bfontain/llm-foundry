# Copyright 2022 MosaicML LLM Foundry authors
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, List, Optional, Type, Union

import torch
import torch_xla.experimental.pjrt_backend
import torch_xla.experimental.pjrt as pjrt

def _cast_if_autocast_enabled(tensor: torch.Tensor) -> torch.Tensor:
    if torch.is_autocast_enabled():
        if tensor.device.type == 'cuda':
            dtype = torch.get_autocast_gpu_dtype()
        elif tensor.device.type == 'cpu':
            dtype = torch.get_autocast_cpu_dtype()
        else:
            raise NotImplementedError()
        return tensor.to(dtype=dtype)
    return tensor


class LPLayerNorm(torch.nn.LayerNorm):

    def __init__(
        self,
        normalized_shape: Union[int, List[int], torch.Size],
        eps: float = 1e-05,
        elementwise_affine: bool = True,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ):
        super().__init__(
            normalized_shape=normalized_shape,
            eps=eps,
            elementwise_affine=elementwise_affine,
            device=device,
            dtype=dtype,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if pjrt.using_pjrt():
            return torch.nn.functional.layer_norm(x, self.normalized_shape, self.weight, self.bias, self.eps)
        module_device = x.device
        downcast_x = _cast_if_autocast_enabled(x)
        downcast_weight = _cast_if_autocast_enabled(
            self.weight) if self.weight is not None else self.weight
        downcast_bias = _cast_if_autocast_enabled(
            self.bias) if self.bias is not None else self.bias
        with torch.autocast(enabled=False, device_type=module_device.type):
            return torch.nn.functional.layer_norm(
                downcast_x,
                self.normalized_shape,
                downcast_weight,
                downcast_bias,
                self.eps,
            )


def rms_norm(x: torch.Tensor,
             weight: Optional[torch.Tensor] = None,
             eps: float = 1e-5) -> torch.Tensor:
    output = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps)
    if weight is not None:
        return output * weight
    return output


class RMSNorm(torch.nn.Module):

    def __init__(
        self,
        normalized_shape: Union[int, List[int], torch.Size],
        eps: float = 1e-5,
        weight: bool = True,
        dtype: Optional[torch.dtype] = None,
        device: Optional[torch.device] = None,
    ):
        super().__init__()
        self.eps = eps
        if weight:
            self.weight = torch.nn.Parameter(
                torch.ones(normalized_shape, dtype=dtype, device=device))
        else:
            self.register_parameter('weight', None)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if pjrt.using_pjrt():
            return rms_norm(x, self.weight, self.eps).to(dtype=x.dtype)
        return rms_norm(x.float(), self.weight, self.eps).to(dtype=x.dtype)


class LPRMSNorm(RMSNorm):

    def __init__(
        self,
        normalized_shape: Union[int, List[int], torch.Size],
        eps: float = 1e-5,
        weight: bool = True,
        dtype: Optional[torch.dtype] = None,
        device: Optional[torch.device] = None,
    ):
        super().__init__(
            normalized_shape=normalized_shape,
            eps=eps,
            weight=weight,
            dtype=dtype,
            device=device,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if pjrt.using_pjrt():
            return rms_norm(x, self.weight, self.eps).to(dtype=x.dtype)
        downcast_x = _cast_if_autocast_enabled(x)
        downcast_weight = _cast_if_autocast_enabled(
            self.weight) if self.weight is not None else self.weight
        with torch.autocast(enabled=False, device_type=x.device.type):
            return rms_norm(downcast_x, downcast_weight,
                            self.eps).to(dtype=x.dtype)


NORM_CLASS_REGISTRY: Dict[str, Type[torch.nn.Module]] = {
    'layernorm': torch.nn.LayerNorm,
    'low_precision_layernorm': LPLayerNorm,
    'rmsnorm': RMSNorm,
    'low_precision_rmsnorm': LPRMSNorm,
}
