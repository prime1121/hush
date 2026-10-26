class RawRecorder extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buffer = new Float32Array(4096);
    this.used = 0;
    this.frames = 0;
    this.maxFrames = options.processorOptions.maxFrames;
    this.finished = false;
    this.port.onmessage = (event) => {
      if (event.data.type === 'stop') this.finish();
    };
  }

  flush() {
    if (!this.used) return;
    const chunk = this.buffer.slice(0, this.used);
    this.port.postMessage({ type: 'data', chunk }, [chunk.buffer]);
    this.used = 0;
  }

  finish() {
    if (this.finished) return;
    this.flush();
    this.finished = true;
    this.port.postMessage({ type: 'stopped', frames: this.frames });
  }

  process(inputs) {
    if (this.finished) return false;
    const channels = inputs[0];
    if (!channels.length) return true;
    for (let frame = 0; frame < channels[0].length; frame++) {
      let value = 0;
      for (const channel of channels) value += channel[frame] || 0;
      this.buffer[this.used++] = value / channels.length;
      this.frames++;
      if (this.used === this.buffer.length) this.flush();
      if (this.frames >= this.maxFrames) {
        this.finish();
        return false;
      }
    }
    return true;
  }
}
registerProcessor('raw-recorder', RawRecorder);
