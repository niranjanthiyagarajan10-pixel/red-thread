// The person's own mic, sent to the server as it is spoken. Nothing is recorded in the browser.

export async function openMic(onChunk, onLevel) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const context = new AudioContext();
  await context.audioWorklet.addModule("/static/pcm-worklet.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm16");
  const mute = context.createGain();
  mute.gain.value = 0;
  node.port.onmessage = (event) => {
    if (onLevel) onLevel(event.data.level);
    onChunk(event.data.pcm);
  };
  source.connect(node);
  node.connect(mute).connect(context.destination);
  if (context.state === "suspended") await context.resume();
  return {
    stop() {
      stream.getTracks().forEach((track) => track.stop());
      try {
        node.disconnect();
        source.disconnect();
      } catch (error) {
        // Already disconnected.
      }
      context.close();
    },
  };
}

// Sends audio in order. If the network is slow, chunks wait and go together, so speech never falls behind.
export function audioSender(url, onRefused) {
  let waiting = [];
  let busy = false;
  let open = true;

  async function pump() {
    if (busy || !open || waiting.length === 0) return;
    busy = true;
    const parts = waiting.splice(0, 20);
    const size = parts.reduce((total, part) => total + part.byteLength, 0);
    const body = new Uint8Array(size);
    let offset = 0;
    for (const part of parts) {
      body.set(new Uint8Array(part), offset);
      offset += part.byteLength;
    }
    try {
      const response = await fetch(url, { method: "POST", body, headers: { "Content-Type": "application/octet-stream" } });
      if (response.status === 409) {
        open = false;
        if (onRefused) onRefused();
      }
    } catch (error) {
      // A dropped chunk only costs a word or two. The next one carries on.
    }
    busy = false;
    pump();
  }

  return {
    push(buffer) {
      if (!open) return;
      waiting.push(buffer);
      pump();
    },
    stop() {
      open = false;
      waiting = [];
    },
  };
}
