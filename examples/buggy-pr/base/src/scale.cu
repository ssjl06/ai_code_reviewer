#include <cuda_runtime.h>
#include <stdlib.h>

__global__ void scale_kernel(const float *in, float *out, int n, float f) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) out[i] = in[i] * f;
}

int scale_sync(const float *d_in, float *h_out, int n, float f) {
  float *d_out = NULL;
  if (cudaMalloc(&d_out, n * sizeof(float)) != cudaSuccess) return -1;
  scale_kernel<<<(n + 255) / 256, 256>>>(d_in, d_out, n, f);
  cudaMemcpy(h_out, d_out, n * sizeof(float), cudaMemcpyDeviceToHost);
  cudaFree(d_out);
  return 0;
}
