// A voice note as the 16 kHz mono WAV that Voice Transcribe reads, built in the browser from the mic's PCM chunks.

export function wav(chunks) {
  const size = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const view = new DataView(new ArrayBuffer(44 + size));
  const text = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + size, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, size, true);
  let offset = 44;
  for (const chunk of chunks) {
    new Uint8Array(view.buffer, offset, chunk.byteLength).set(new Uint8Array(chunk));
    offset += chunk.byteLength;
  }
  return new Uint8Array(view.buffer);
}

export function base64(bytes) {
  let out = "";
  for (let index = 0; index < bytes.length; index += 0x8000) out += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(out);
}
