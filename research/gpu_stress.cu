// GPU load at a chosen duty cycle, for research/lut_sweep.py.
//
// A compute-bound kernel (eight independent FMA chains per thread, enough blocks for every SM)
// runs in short launches for DUTY % of each 100 ms period; the GPU idles for the rest.
// About 113 W on an RTX 4080 Laptop GPU. A concurrent memory-bound copy lowered that to 88 W
// (each round waited for the copy), so there is none.
// nvidia-smi's utilization.gpu is the share of time a kernel runs, so it follows DUTY.
// Runs until SECONDS have passed, or forever with 0; stops cleanly on SIGTERM/SIGINT.
//
//   nvcc -O3 -Xcompiler -U_GNU_SOURCE -o research/gpu_stress research/gpu_stress.cu
//   (-U_GNU_SOURCE: glibc 2.43 and CUDA 13.0 declare rsqrtf differently)
//   research/gpu_stress DUTY [SECONDS]
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <thread>

#include <cuda_runtime.h>

static volatile std::sig_atomic_t stop_requested = 0;

static void on_signal(int) { stop_requested = 1; }

#define CHECK(call)                                                                   \
    do {                                                                              \
        cudaError_t err = (call);                                                     \
        if (err != cudaSuccess) {                                                     \
            std::fprintf(stderr, "%s: %s\n", #call, cudaGetErrorString(err));         \
            return 1;                                                                 \
        }                                                                             \
    } while (0)

__global__ void burn(float *out, int iters) {
    float a = threadIdx.x * 1e-4f, b = 1.0001f, c = 0.9999f, d = 0.5f;
    float e = 0.25f, f = 0.125f, g = 1.5f, h = blockIdx.x * 1e-5f;
    for (int i = 0; i < iters; i++) {
        a = fmaf(a, b, c); b = fmaf(b, c, d); c = fmaf(c, d, e); d = fmaf(d, e, f);
        e = fmaf(e, f, g); f = fmaf(f, g, h); g = fmaf(g, h, a); h = fmaf(h, a, b);
    }
    // Never true in practice; keeps the compiler from dropping the loop.
    if (a + b + c + d + e + f + g + h == 1234.5f)
        out[blockIdx.x * blockDim.x + threadIdx.x] = a;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s DUTY(0-100) [SECONDS]\n", argv[0]);
        return 2;
    }
    const int duty = std::atoi(argv[1]);
    const double seconds = argc > 2 ? std::atof(argv[2]) : 0;
    if (duty < 0 || duty > 100) {
        std::fprintf(stderr, "duty must be 0-100\n");
        return 2;
    }
    std::signal(SIGTERM, on_signal);
    std::signal(SIGINT, on_signal);

    cudaDeviceProp prop;
    CHECK(cudaGetDeviceProperties(&prop, 0));
    const int threads = 256, blocks = prop.multiProcessorCount * 8;
    float *out;
    CHECK(cudaMalloc(&out, sizeof(float) * threads * blocks));

    // About 2 ms per launch, so the duty cycle is followed closely.
    int iters = 1 << 12;
    for (;;) {
        auto t0 = std::chrono::steady_clock::now();
        burn<<<blocks, threads>>>(out, iters);
        CHECK(cudaDeviceSynchronize());
        double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        if (ms >= 2.0 || iters >= (1 << 24))
            break;
        iters *= 2;
    }
    std::printf("%s: %d SMs, %d blocks x %d threads, %d iterations per launch, duty %d %%\n",
                prop.name, prop.multiProcessorCount, blocks, threads, iters, duty);
    std::fflush(stdout);

    const auto start = std::chrono::steady_clock::now();
    const auto period = std::chrono::milliseconds(100);
    const auto busy = std::chrono::microseconds(duty * 1000);
    while (!stop_requested) {
        const auto cycle = std::chrono::steady_clock::now();
        if (seconds > 0 && std::chrono::duration<double>(cycle - start).count() >= seconds)
            break;
        while (duty > 0 && !stop_requested && std::chrono::steady_clock::now() - cycle < busy) {
            burn<<<blocks, threads>>>(out, iters);
            CHECK(cudaDeviceSynchronize());
        }
        std::this_thread::sleep_until(cycle + period);
    }
    CHECK(cudaFree(out));
    return 0;
}
