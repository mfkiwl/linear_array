import time
import numpy as np
from queue import deque
from queue import Queue
from scipy.fftpack import fft, fftshift

global cycle
cycle = 0


def DFT(x):
    """Compute the Discrete Fourier Transform of the 1D array x"""
    N = len(x)
    n = np.arange(N)
    k = n.reshape((N, 1))
    M = np.exp(-2j * np.pi * k * n / N)
    return np.dot(M, x)


def FFT(x):
    """A recursive implementation of the 1D Cooley-Tukey FFT"""
    N = len(x)
    if N % 2 > 0:
        raise ValueError("size of x must be a power of 2")
    elif N <= 2:  # this cutoff should be optimized
        return DFT(x)
    else:
        X_even = FFT(x[::2])
        X_odd = FFT(x[1::2])
        factor = np.exp(-2.0j * np.pi * np.arange(N) / N)
        return np.concatenate([X_even + factor[:N // 2] * X_odd,
                               X_even + factor[N // 2:] * X_odd])


def FFT_vectorized(x):
    """A vectorized, non-recursive version of the Cooley-Tukey FFT"""
    N = len(x)
    if np.log2(N) % 1 > 0:
        raise ValueError("size of x must be a power of 2")

    # N_min here is equivalent to the stopping condition above,
    # and should be a power of 2
    N_min = min(N, 2)

    # Perform an O[N^2] DFT on all length-N_min sub-problems at once
    n = np.arange(N_min)
    k = n[:, None]
    M = np.exp(-2j * np.pi * n * k / N_min)
    X = np.dot(M, np.array(x).reshape((N_min, -1)))

    # build-up each level of the recursive calculation all at once
    while X.shape[0] < N:
        X_even = X[:, :X.shape[1] // 2]
        X_odd = X[:, X.shape[1] // 2:]
        factor = np.exp(-1j * np.pi * np.arange(X.shape[0])
                        / X.shape[0])[:, None]
        X = np.vstack([X_even + factor * X_odd,
                       X_even - factor * X_odd])

    return X.ravel()


def complex_mult(x, y):
    """Complex multiplication: (X * conjugate(X))"""
    global cycle
    list_3 = []
    for i in range(len(x)):
        real_part = x[i].real * y[i].real - x[i].imag * y[i].imag
        image_part = x[i].imag * y[i].real + x[i].real * y[i].imag
        complex_result = real_part + image_part * 1j
        list_3.append(complex_result)
        cycle += 1

    return list_3


def getTwiddle(NFFT):
    """Generate the twiddle factors"""
    W = np.r_[[1.0 + 1.0j] * NFFT]
    for k in range(NFFT):
        W[k] = np.exp(-2.0j * np.pi * k / NFFT)

    return W


def rFFT(x):
    """
    Recursive FFT implementation.
    References
      -- http://www.cse.uiuc.edu/iem/fft/rcrsvfft/
      -- "A Simple and Efficient FFT Implementation in C++"
          by Vlodymyr Myrnyy
    """
    n = len(x)
    if n == 1:
        return x

    w = getTwiddle(n)
    m = n // 2
    X = np.ones(m, float) * 1j
    Y = np.ones(m, float) * 1j

    for k in range(m):
        X[k] = x[2 * k]
        Y[k] = x[2 * k + 1]

    X = rFFT(X)
    Y = rFFT(Y)
    F = np.ones(n, float) * 1j

    for k in range(n):
        i = (k % m)
        F[k] = X[i] + w[k] * Y[i]
        global cycle
        cycle += 1

    return F


class LinearArrayCell:
    def __init__(self, cell_size):
        self.cell_size = cell_size
        self.cell_index = 0
        self.cell_input = None
        self.single_in = 0
        self.single_out = 0
        self.data_to_compute_1 = Queue(maxsize=self.cell_size)
        self.data_to_compute_2 = Queue(maxsize=self.cell_size)
        self.alpha = []
        self.alpha_top = []
        self.alpha_bottom = []
        self.cell_shift = Queue()
        self.cell_partial_result = Queue()
        self.cell_output = Queue()
        self.signal_index = 0

    def connect(self, cell_index, array, array_size, iterations):
        self.cell_index = cell_index
        if iterations % array_size == 0:  # a group of data completed loop in all cells
            if self.signal_index > 0:
                self.clear_shift()
            self.cell_input = array.input[self.signal_index][self.cell_index]
            self.signal_index += 1
        else:
            self.cell_input = array.cells[self.cell_index - 1]  # shift registers

    def cell_read(self):  # load all data needed for a cell
        global cycle
        if type(self.cell_input) is Queue:  # from input FIFO
            for _ in range(self.cell_size):
                if self.cell_input.empty():
                    self.single_in = 0
                else:
                    self.single_in = self.cell_input.get()
                self.data_to_compute_1.put(self.single_in)
                self.data_to_compute_2.put(self.single_in.real - self.single_in.imag * 1j)  # conjugate
                cycle += 1
        else:  # from shift registers (only for data, not for conjugate(data))
            for _ in range(self.cell_size):
                self.single_in = self.cell_input.cell_shift.get()
                self.data_to_compute_1.put(self.single_in)
                # self.data_to_compute_2.put(self.single_in.real - self.single_in.imag * 1j)
                cycle += 1

    def compute(self, last_cell, prev_alpha, iterations, total_iterations):
        list_1 = list(self.data_to_compute_1.queue)
        list_2 = list(self.data_to_compute_2.queue)
        cm = complex_mult(list_1, list_2)
        fft_res = rFFT(cm)
        # print(f'Compare rFFT with built-in FFT at PE {iterations}:', np.allclose(rFFT(cm), fft(cm)))
        fft_shift = fftshift(fft_res)[len(fft_res) // 2 - 8: len(fft_res) // 2 + 8]  # fft_res[8:23]
        global cycle
        fft_abs = np.abs(fft_shift)
        cycle += 16
        if iterations == 0:
            self.alpha_top = fft_abs[len(fft_abs) // 2: len(fft_abs)]  # previous top: fft_abs[8:15]
        else:
            if not last_cell:
                self.alpha = self.alpha_top  # initialize alpha with previous top: fft_abs[8:15]
                self.alpha_bottom = fft_abs[0: len(fft_abs) // 2]  # current bottom: fft_abs[0:7]
                for i in range(len(fft_abs) // 2):
                    if self.alpha[i] < self.alpha_bottom[i]:  # alpha = max(previous top, current bottom)
                        self.alpha[i] = self.alpha_bottom[i]  # update alpha
                    if self.alpha[i] < prev_alpha[i]:  # alpha Vs. previous alpha
                        self.alpha[i] = prev_alpha[i]  # update alpha
                    cycle += 2
                # self.alpha = self.alpha_top
                self.alpha_top = fft_abs[len(fft_abs) // 2: len(fft_abs)]  # update alpha_top to current iteration
            else:
                for i in range(len(fft_abs) // 2):
                    if self.alpha_top[i] < prev_alpha[i]:  # top Vs. current bottom
                        self.alpha_top[i] = prev_alpha[i]
                    cycle += 1
                self.alpha = self.alpha_top  # final output: alpha

    def shift(self):
        global cycle
        for _ in range(self.cell_size):
            self.single_out = self.data_to_compute_1.get()
            self.cell_shift.put(self.single_out)
            cycle += 1

    def clear_shift(self):  # to clear shift data from cell_shift queue when complete an input signal
        for _ in range(self.cell_size):
            self.cell_shift.get()
            self.data_to_compute_2.get()


class LinearArray:
    def __init__(self, array_size, cell_size, fifo_input):
        self.array_size = array_size
        self.cell_size = cell_size
        self.input = fifo_input
        self.iterations = 0
        self.cells = []
        self.result = []

        for _ in range(self.array_size):
            cell = LinearArrayCell(self.cell_size)
            self.cells.append(cell)
        self.num_cells = len(self.cells)

    def connect(self):
        for cell_index, cell in enumerate(self.cells):
            cell.connect(cell_index, self, self.array_size, self.iterations)

    def read(self):
        for cell in self.cells:
            cell.cell_read()

    def compute(self, total_iterations):
        last_cell = False
        for i in range(self.num_cells):
            if i == self.num_cells - 1:
                last_cell = True
            if i == 0:  # iteration 0: prev_alpha = [0, 0, 0, 0, 0, 0, 0, 0]
                self.cells[i].compute(last_cell, [0 for _ in range(8)], self.iterations, total_iterations)
            else:  # iteration i: prev_alpha = cells[i-1].alpha
                self.cells[i].compute(last_cell, self.cells[i - 1].alpha, self.iterations, total_iterations)
        if self.iterations > 0:
            self.num_cells -= 1

    def shift(self):
        for cell in self.cells:
            cell.shift()

    def run(self, total_iterations):
        for _ in range(total_iterations):
            self.connect()
            self.read()
            self.compute(total_iterations)
            self.shift()
            self.iterations += 1
        self.result.append(self.cells[0].alpha_top)
        self.cells.pop(0)
        for cell in self.cells:
            self.result.append(cell.alpha)
        return self.result


def main():
    # print("Hello World!")
    signals = 1
    pes = 4  # 256
    registers = 32
    total_iter = signals * pes
    input_queue = [[Queue() for _ in range(pes)] for _ in range(signals)]
    read_cycle = registers * pes
    compute_cycle = registers + registers * np.log2(registers)
    shift_cycle = registers
    total_cycle = int(read_cycle + compute_cycle + (shift_cycle + compute_cycle) * (pes - 1))
    total_time = total_cycle * 2 / 1000
    print('Theoretical number of cycles = {:d}. FPGA time = {:f} us at 500MHz.'.format(total_cycle, total_time))

    for signal in range(signals):
        for pe in range(pes):
            if signal == 0:
                for index in range(pe * registers, (pe + 1) * registers):
                    complex_data = index / 128 + (index + 1) / 128 * 1j
                    input_queue[signal][pe].put(complex_data)
            if signal > 0:
                for index in reversed(range(pe * registers, (pe + 1) * registers)):
                    complex_data = index / 128 + (index + 1) / 128 * 1j
                    input_queue[signal][pe].put(complex_data)

    # print(list(input_queue[0][-1].queue))
    myArray = LinearArray(pes, registers, input_queue)
    start_time = time.time()
    scd = myArray.run(total_iter)  # run (signal*pes) times
    end_time = time.time()
    cpu_time = end_time - start_time
    pe_cycle = cycle // pes
    print('----{:.4f} seconds on CPU----'.format(cpu_time))
    print('Real total number of cycles on PE = {}'.format(pe_cycle))
    # print('----alpha profile of SCD matrix----')
    for index, alpha in enumerate(scd):
        # print('alpha[{:d}] = {:f}'.format(index, *alpha))
        # print(len(alpha))
        print('alpha[{:d}] = {}'.format(index, [np.round(element, 4) for element in alpha]))
    '''
    for i in range(signals):
        for j in range(pes):
            print('PE[{:d}]: {}'.format(j, ['%.5f, %.5f' % (ele.real, ele.imag) for ele in list(scd[i][j].queue)]))
            print(len(list(scd[i][j].queue)))
    '''


if __name__ == "__main__":
    main()
