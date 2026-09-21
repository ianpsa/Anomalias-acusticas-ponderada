class FerrisCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = []; this.phase = 0; }
  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    // Downsample by averaging source samples; hardware may ignore requested sampleRate.
    if (input) {
      const ratio = sampleRate / 16000;
      this.sum ??= 0; this.count ??= 0;
      for (const sample of input) {
        this.sum += sample; this.count++; this.phase++;
        if (this.phase >= ratio) {
          this.buffer.push(this.sum / this.count); this.sum = 0; this.count = 0; this.phase -= ratio;
        }
      }
      if (this.buffer.length >= 800) {
        const chunk = new Float32Array(this.buffer.splice(0, 800)); this.port.postMessage(chunk, [chunk.buffer]);
      }
    }
    // Output stays silent: never feed microphone audio back to the speakers.
    for (const channel of outputs[0] || []) channel.fill(0);
    return true;
  }
}
registerProcessor('ferris-capture', FerrisCapture);
