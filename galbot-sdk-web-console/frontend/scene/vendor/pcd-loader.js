import { decompressLZF } from './decompress-lzf.js';

/**
 * PCD parser: ascii, binary, binary_compressed (xyz / xyzrgb).
 * Returns { positions: Float32Array, colors: Float32Array|null, count }
 */
export async function loadPCD(file) {
  const buffer = await file.arrayBuffer();
  const headerText = readHeaderText(buffer);
  const header = parseHeader(headerText);

  if (!header.fields.includes('x') || !header.fields.includes('y') || !header.fields.includes('z')) {
    throw new Error('PCD must contain x, y, z fields');
  }

  const dataStart = headerText.length;
  let positions;
  let colors = null;

  if (header.data === 'ascii') {
    const text = new TextDecoder().decode(buffer.slice(dataStart));
    ({ positions, colors } = parseAsciiData(text, header));
  } else if (header.data === 'binary') {
    ({ positions, colors } = parseBinaryData(buffer.slice(dataStart), header));
  } else if (header.data === 'binary_compressed') {
    ({ positions, colors } = parseBinaryCompressedData(buffer.slice(dataStart), header));
  } else {
    throw new Error(`Unsupported PCD data type: ${header.data}`);
  }

  return { positions, colors, count: header.points, header };
}

function readHeaderText(buffer) {
  const bytes = new Uint8Array(buffer);
  let end = 0;
  for (let i = 0; i < bytes.length - 3; i++) {
    if (bytes[i] === 68 && bytes[i + 1] === 65 && bytes[i + 2] === 84 && bytes[i + 3] === 65) {
      end = i + 4;
      while (end < bytes.length && bytes[end] !== 10) end++;
      end++;
      break;
    }
  }
  if (end === 0) throw new Error('Invalid PCD: DATA section not found');
  return new TextDecoder().decode(bytes.slice(0, end));
}

function parseHeader(text) {
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  const header = {
    version: '0.7',
    fields: [],
    size: [],
    type: [],
    count: [],
    width: 0,
    height: 1,
    points: 0,
    data: 'ascii',
    offsets: {},
  };

  for (const line of lines) {
    const [key, ...rest] = line.split(/\s+/);
    const val = rest.join(' ');
    switch (key) {
      case 'VERSION': header.version = val; break;
      case 'FIELDS': header.fields = rest; break;
      case 'SIZE': header.size = rest.map(Number); break;
      case 'TYPE': header.type = rest; break;
      case 'COUNT': header.count = rest.map(Number); break;
      case 'WIDTH': header.width = Number(val); break;
      case 'HEIGHT': header.height = Number(val); break;
      case 'POINTS': header.points = Number(val); break;
      case 'DATA': header.data = val.toLowerCase(); break;
      default: break;
    }
  }

  if (header.points === 0) header.points = header.width * header.height;
  if (header.count.length === 0) header.count = header.fields.map(() => 1);

  computeFieldOffsets(header);
  return header;
}

function computeFieldOffsets(header) {
  let sizeSum = 0;
  header.offsets = {};
  for (let j = 0; j < header.fields.length; j++) {
    header.offsets[header.fields[j]] = sizeSum;
    if (header.data === 'binary_compressed') {
      sizeSum += header.size[j] * header.count[j] * header.points;
    } else {
      sizeSum += header.size[j] * header.count[j];
    }
  }
  header.rowSize = sizeSum;
}

function fieldByteSize(header, fieldIndex) {
  return header.size[fieldIndex] * header.count[fieldIndex];
}

function hasColor(header) {
  return header.fields.includes('rgb')
    || (header.fields.includes('r') && header.fields.includes('g') && header.fields.includes('b'));
}

function readFieldAt(data, header, fieldName, pointIndex, baseAddr = 0) {
  const fi = header.fields.indexOf(fieldName);
  if (fi < 0) return 0;

  const fieldBytes = fieldByteSize(header, fi);
  let addr;
  if (header.data === 'binary_compressed') {
    addr = baseAddr + header.offsets[fieldName] + pointIndex * fieldBytes;
  } else {
    addr = baseAddr + header.offsets[fieldName];
  }

  const size = header.size[fi];
  const type = header.type[fi];
  if (type === 'F' && size === 4) return data.getFloat32(addr, true);
  if (type === 'F' && size === 8) return data.getFloat64(addr, true);
  if (type === 'U' && size === 1) return data.getUint8(addr);
  if (type === 'U' && size === 2) return data.getUint16(addr, true);
  if (type === 'U' && size === 4) return data.getUint32(addr, true);
  if (type === 'I' && size === 4) return data.getInt32(addr, true);
  return 0;
}

function extractPositionsColors(data, header) {
  const n = header.points;
  const positions = new Float32Array(n * 3);
  const colors = hasColor(header) ? new Float32Array(n * 3) : null;

  for (let i = 0; i < n; i++) {
    const base = header.data === 'binary' ? i * header.rowSize : 0;
    positions[i * 3] = readFieldAt(data, header, 'x', i, base);
    positions[i * 3 + 1] = readFieldAt(data, header, 'y', i, base);
    positions[i * 3 + 2] = readFieldAt(data, header, 'z', i, base);

    if (colors) {
      if (header.fields.includes('r')) {
        colors[i * 3] = readFieldAt(data, header, 'r', i, base) / 255;
        colors[i * 3 + 1] = readFieldAt(data, header, 'g', i, base) / 255;
        colors[i * 3 + 2] = readFieldAt(data, header, 'b', i, base) / 255;
      } else if (header.fields.includes('rgb')) {
        const rgb = readFieldAt(data, header, 'rgb', i, base);
        const packed = new Float32Array(1);
        packed[0] = rgb;
        const view = new Uint8Array(packed.buffer);
        colors[i * 3] = view[2] / 255;
        colors[i * 3 + 1] = view[1] / 255;
        colors[i * 3 + 2] = view[0] / 255;
      }
    }
  }

  return { positions, colors };
}

function parseAsciiData(text, header) {
  const lines = text.split('\n').filter((l) => l.trim() && !l.startsWith('#'));
  const n = lines.length;
  const positions = new Float32Array(n * 3);
  const colors = hasColor(header) ? new Float32Array(n * 3) : null;

  const xi = header.fields.indexOf('x');
  const yi = header.fields.indexOf('y');
  const zi = header.fields.indexOf('z');
  const rgbIdx = header.fields.indexOf('rgb');
  const rIdx = header.fields.indexOf('r');
  const gIdx = header.fields.indexOf('g');
  const bIdx = header.fields.indexOf('b');

  for (let i = 0; i < n; i++) {
    const parts = lines[i].trim().split(/\s+/).map(Number);
    positions[i * 3] = parts[xi];
    positions[i * 3 + 1] = parts[yi];
    positions[i * 3 + 2] = parts[zi];

    if (colors) {
      if (rIdx >= 0 && gIdx >= 0 && bIdx >= 0) {
        colors[i * 3] = parts[rIdx] / 255;
        colors[i * 3 + 1] = parts[gIdx] / 255;
        colors[i * 3 + 2] = parts[bIdx] / 255;
      } else if (rgbIdx >= 0) {
        const rgb = parts[rgbIdx];
        colors[i * 3] = ((rgb >> 16) & 0xff) / 255;
        colors[i * 3 + 1] = ((rgb >> 8) & 0xff) / 255;
        colors[i * 3 + 2] = (rgb & 0xff) / 255;
      }
    }
  }

  return { positions, colors };
}

function parseBinaryData(buffer, header) {
  return extractPositionsColors(new DataView(buffer), header);
}

function parseBinaryCompressedData(buffer, header) {
  const view = new DataView(buffer);
  if (buffer.byteLength < 8) throw new Error('binary_compressed PCD: truncated header');

  const compressedSize = view.getUint32(0, true);
  const decompressedSize = view.getUint32(4, true);
  const compressed = new Uint8Array(buffer, 8, compressedSize);

  const decompressed = decompressLZF(compressed, decompressedSize);
  return extractPositionsColors(new DataView(decompressed.buffer, decompressed.byteOffset, decompressed.byteLength), header);
}
