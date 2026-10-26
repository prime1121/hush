'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function makeRecorder(maxFrames = 48000) {
  let Recorder;
  const messages = [];
  class AudioWorkletProcessor {
    constructor() {
      this.port = { postMessage: (message) => messages.push(message) };
    }
  }
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, 'static', 'recorder-worklet.js'), 'utf8'),
    {
      AudioWorkletProcessor,
      Float32Array,
      registerProcessor(name, constructor) {
        assert.equal(name, 'raw-recorder');
        Recorder = constructor;
      },
    },
    { filename: 'recorder-worklet.js' },
  );
  const recorder = new Recorder({ processorOptions: { maxFrames } });
  return { recorder, messages };
}

function capturedSamples(messages) {
  return messages.filter((message) => message.type === 'data').flatMap((message) => Array.from(message.chunk));
}

test('manual stop preserves the complete partial audio chunk before acknowledgement', () => {
  const { recorder, messages } = makeRecorder();
  const expected = [];
  for (let block = 0; block < 35; block++) {
    const input = Float32Array.from({ length: 128 }, (_, index) => (block * 128 + index) / 48000);
    expected.push(...input);
    assert.equal(recorder.process([[input]]), true);
  }
  assert.equal(capturedSamples(messages).length, 4096);
  recorder.port.onmessage({ data: { type: 'stop' } });
  assert.deepEqual(capturedSamples(messages), expected);
  const chunks = messages.filter((message) => message.type === 'data');
  assert.equal(chunks[1].chunk.length, 384);
  assert.equal(messages.at(-1).type, 'stopped');
  assert.equal(messages.at(-1).frames, 4480);
  const count = messages.length;
  recorder.port.onmessage({ data: { type: 'stop' } });
  assert.equal(recorder.process([[new Float32Array(128)]]), false);
  assert.equal(messages.length, count, 'stop must be idempotent');
});

test('stereo input is averaged into mono without discarding either channel', () => {
  const { recorder, messages } = makeRecorder();
  recorder.process([[new Float32Array(128).fill(0.5), new Float32Array(128).fill(-0.25)]]);
  recorder.port.onmessage({ data: { type: 'stop' } });
  assert.equal(capturedSamples(messages).length, 128);
  assert.ok(capturedSamples(messages).every((sample) => sample === 0.125));
});

test('duration cap stops at the exact frame even within a rendering block', () => {
  const { recorder, messages } = makeRecorder(250);
  assert.equal(recorder.process([[new Float32Array(128).fill(0.5)]]), true);
  assert.equal(recorder.process([[new Float32Array(128).fill(-0.5)]]), false);
  const samples = capturedSamples(messages);
  assert.equal(samples.length, 250);
  assert.ok(samples.slice(0, 128).every((sample) => sample === 0.5));
  assert.ok(samples.slice(128).every((sample) => sample === -0.5));
  assert.equal(messages.at(-1).type, 'stopped');
  assert.equal(messages.at(-1).frames, 250);
});

test('missing input produces no fake samples and can still stop cleanly', () => {
  const { recorder, messages } = makeRecorder();
  assert.equal(recorder.process([[]]), true);
  recorder.port.onmessage({ data: { type: 'stop' } });
  assert.equal(capturedSamples(messages).length, 0);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].type, 'stopped');
  assert.equal(messages[0].frames, 0);
});
