// Turns the mic into 16 kHz mono PCM16, in 100 ms chunks, which is what live Voice Transcribe expects.
class Pcm16 extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.position = 0;
    this.chunk = new Int16Array(1600);
    this.filled = 0;
    this.energy = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    let position = this.position;
    while (position < input.length) {
      const index = Math.floor(position);
      const fraction = position - index;
      const a = input[index];
      const b = index + 1 < input.length ? input[index + 1] : a;
      const sample = Math.max(-1, Math.min(1, a + (b - a) * fraction));
      this.chunk[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      this.energy += sample * sample;
      if (this.filled === this.chunk.length) {
        const level = Math.sqrt(this.energy / this.filled);
        this.port.postMessage({ pcm: this.chunk.buffer, level }, [this.chunk.buffer]);
        this.chunk = new Int16Array(1600);
        this.filled = 0;
        this.energy = 0;
      }
      position += this.step;
    }
    this.position = position - input.length;
    return true;
  }
}

registerProcessor("pcm16", Pcm16);
