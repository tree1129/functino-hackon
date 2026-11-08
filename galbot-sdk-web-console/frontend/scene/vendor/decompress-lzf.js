/** Marc Lehmann LZF decompress (PCL binary_compressed PCD). */
export function decompressLZF(inData, outLength) {
  const inLength = inData.length;
  const outData = new Uint8Array(outLength);
  let inPtr = 0;
  let outPtr = 0;

  do {
    let ctrl = inData[inPtr++];
    if (ctrl < (1 << 5)) {
      ctrl += 1;
      if (outPtr + ctrl > outLength) throw new Error('LZF: output buffer too small');
      if (inPtr + ctrl > inLength) throw new Error('LZF: invalid compressed data');
      do {
        outData[outPtr++] = inData[inPtr++];
      } while (--ctrl);
    } else {
      let len = ctrl >> 5;
      let ref = outPtr - ((ctrl & 0x1f) << 8) - 1;
      if (inPtr >= inLength) throw new Error('LZF: invalid compressed data');
      if (len === 7) {
        len += inData[inPtr++];
        if (inPtr >= inLength) throw new Error('LZF: invalid compressed data');
      }
      ref -= inData[inPtr++];
      if (outPtr + len + 2 > outLength) throw new Error('LZF: output buffer too small');
      if (ref < 0 || ref >= outPtr) throw new Error('LZF: invalid back-reference');
      do {
        outData[outPtr++] = outData[ref++];
      } while (--len + 2);
    }
  } while (inPtr < inLength);

  return outData;
}
