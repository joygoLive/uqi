import perceval as pcvl
import perceval.components as comp

# Hong-Ou-Mandel interference
# 2-photon, 2-mode beamsplitter circuit
# Simple but fundamental: demonstrates photon bunching

circuit = pcvl.Circuit(2)
circuit.add(0, comp.BS())  # 50/50 beamsplitter

# Input: one photon per mode
input_state = pcvl.BasicState([1, 1])

processor = pcvl.Processor("SLOS", circuit)
processor.with_input(input_state)
processor.min_detected_photons_filter(0)

from perceval.algorithm import Sampler
sampler = Sampler(processor)
result = sampler.sample_count(100)
