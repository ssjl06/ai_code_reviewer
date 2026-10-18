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

// Returns a host copy of the scaled data; caller owns the result.
float *scale_to_host(const float *d_in, int n, float f, cudaStream_t stream) {
  float *d_out = NULL;
  float *h_tmp = (float *)malloc(n * sizeof(float));
  float *h_res = (float *)malloc(n * sizeof(float));
  if (!h_tmp || !h_res) return NULL;
  cudaMalloc(&d_out, n * sizeof(float));
  scale_kernel<<<(n + 255) / 256, 256, 0, stream>>>(d_in, d_out, n, f);
  cudaMemcpyAsync(h_tmp, d_out, n * sizeof(float), cudaMemcpyDeviceToHost, stream);
  for (int i = 0; i < n; i++) h_res[i] = h_tmp[i];
  free(h_tmp);
  cudaFree(d_out);
  return h_res;
}
