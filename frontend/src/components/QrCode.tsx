import { useMemo } from "react";

interface BlockSpec {
  data: number;
  ecc: number;
  count: number;
}

const BLOCKS: Record<number, BlockSpec[]> = {
  1: [{ data: 19, ecc: 7, count: 1 }],
  2: [{ data: 34, ecc: 10, count: 1 }],
  3: [{ data: 55, ecc: 15, count: 1 }],
  4: [{ data: 80, ecc: 20, count: 1 }],
  5: [{ data: 108, ecc: 26, count: 1 }],
  6: [{ data: 68, ecc: 18, count: 2 }],
  7: [{ data: 78, ecc: 20, count: 2 }],
  8: [{ data: 97, ecc: 24, count: 2 }],
  9: [{ data: 116, ecc: 30, count: 2 }],
  10: [
    { data: 68, ecc: 18, count: 2 },
    { data: 69, ecc: 18, count: 2 },
  ],
};

const ALIGNMENT: Record<number, number[]> = {
  1: [],
  2: [6, 18],
  3: [6, 22],
  4: [6, 26],
  5: [6, 30],
  6: [6, 34],
  7: [6, 22, 38],
  8: [6, 24, 42],
  9: [6, 26, 46],
  10: [6, 28, 50],
};

export function QrCode({ value, size = 256 }: { value: string; size?: number }) {
  const matrix = useMemo(() => qrMatrix(value), [value]);
  const modules = matrix.length;
  const quiet = 4;
  const view = modules + quiet * 2;
  const cells: JSX.Element[] = [];
  for (let y = 0; y < modules; y += 1) {
    for (let x = 0; x < modules; x += 1) {
      if (!matrix[y][x]) continue;
      cells.push(
        <rect
          key={`${x}:${y}`}
          x={x + quiet}
          y={y + quiet}
          width="1"
          height="1"
          fill="currentColor"
        />,
      );
    }
  }
  return (
    <svg
      aria-label="Mobile pairing QR code"
      role="img"
      viewBox={`0 0 ${view} ${view}`}
      width={size}
      height={size}
      style={{ background: "white", color: "black", maxWidth: "100%", height: "auto" }}
      shapeRendering="crispEdges"
    >
      {cells}
    </svg>
  );
}

export function qrMatrix(value: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(value));
  const version = chooseVersion(bytes.length);
  const data = encodeData(bytes, version);
  const allCodewords = addErrorCorrection(data, version);
  return drawMatrix(allCodewords, version);
}

function chooseVersion(byteLength: number): number {
  for (let version = 1; version <= 10; version += 1) {
    const dataCodewords = BLOCKS[version].reduce(
      (sum, spec) => sum + spec.data * spec.count,
      0,
    );
    const countBits = version <= 9 ? 8 : 16;
    if (4 + countBits + byteLength * 8 <= dataCodewords * 8) return version;
  }
  throw new Error("Pairing QR payload is too large");
}

function encodeData(bytes: number[], version: number): number[] {
  const capacity = BLOCKS[version].reduce(
    (sum, spec) => sum + spec.data * spec.count,
    0,
  );
  const bits: number[] = [];
  appendBits(bits, 0b0100, 4);
  appendBits(bits, bytes.length, version <= 9 ? 8 : 16);
  for (const byte of bytes) appendBits(bits, byte, 8);

  const capacityBits = capacity * 8;
  for (let i = 0; i < 4 && bits.length < capacityBits; i += 1) bits.push(0);
  while (bits.length % 8 !== 0) bits.push(0);

  const result: number[] = [];
  for (let offset = 0; offset < bits.length; offset += 8) {
    let value = 0;
    for (let bit = 0; bit < 8; bit += 1) value = (value << 1) | bits[offset + bit];
    result.push(value);
  }
  let pad = 0;
  while (result.length < capacity) {
    result.push(pad % 2 === 0 ? 0xec : 0x11);
    pad += 1;
  }
  return result;
}

function addErrorCorrection(data: number[], version: number): number[] {
  const blocks: number[][] = [];
  const specs: BlockSpec[] = [];
  let offset = 0;
  for (const spec of BLOCKS[version]) {
    for (let index = 0; index < spec.count; index += 1) {
      blocks.push(data.slice(offset, offset + spec.data));
      specs.push(spec);
      offset += spec.data;
    }
  }
  const eccBlocks = blocks.map((block, index) => reedSolomon(block, specs[index].ecc));
  const result: number[] = [];
  const maxData = Math.max(...blocks.map((block) => block.length));
  for (let column = 0; column < maxData; column += 1) {
    for (const block of blocks) {
      if (column < block.length) result.push(block[column]);
    }
  }
  const eccLength = specs[0].ecc;
  for (let column = 0; column < eccLength; column += 1) {
    for (const block of eccBlocks) result.push(block[column]);
  }
  return result;
}

function reedSolomon(data: number[], degree: number): number[] {
  let generator = [1];
  for (let exponent = 0; exponent < degree; exponent += 1) {
    generator = multiplyPolynomials(generator, [1, gfPow2(exponent)]);
  }
  const remainder = new Array<number>(degree).fill(0);
  for (const byte of data) {
    const factor = byte ^ remainder[0];
    remainder.shift();
    remainder.push(0);
    for (let index = 0; index < degree; index += 1) {
      remainder[index] ^= gfMultiply(generator[index + 1], factor);
    }
  }
  return remainder;
}

function multiplyPolynomials(left: number[], right: number[]): number[] {
  const result = new Array<number>(left.length + right.length - 1).fill(0);
  for (let i = 0; i < left.length; i += 1) {
    for (let j = 0; j < right.length; j += 1) {
      result[i + j] ^= gfMultiply(left[i], right[j]);
    }
  }
  return result;
}

function gfPow2(exponent: number): number {
  let value = 1;
  for (let index = 0; index < exponent; index += 1) {
    value <<= 1;
    if (value & 0x100) value ^= 0x11d;
  }
  return value;
}

function gfMultiply(left: number, right: number): number {
  let a = left;
  let b = right;
  let result = 0;
  while (b > 0) {
    if (b & 1) result ^= a;
    b >>= 1;
    a <<= 1;
    if (a & 0x100) a ^= 0x11d;
  }
  return result;
}

function drawMatrix(codewords: number[], version: number): boolean[][] {
  const size = 17 + version * 4;
  const modules = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const reserved = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));

  const setFunction = (x: number, y: number, dark: boolean) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    modules[y][x] = dark;
    reserved[y][x] = true;
  };

  drawFinder(setFunction, 3, 3);
  drawFinder(setFunction, size - 4, 3);
  drawFinder(setFunction, 3, size - 4);

  for (let index = 8; index < size - 8; index += 1) {
    if (!reserved[6][index]) setFunction(index, 6, index % 2 === 0);
    if (!reserved[index][6]) setFunction(6, index, index % 2 === 0);
  }

  for (const y of ALIGNMENT[version]) {
    for (const x of ALIGNMENT[version]) {
      if (reserved[y][x]) continue;
      for (let dy = -2; dy <= 2; dy += 1) {
        for (let dx = -2; dx <= 2; dx += 1) {
          setFunction(x + dx, y + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
        }
      }
    }
  }

  reserveFormat(setFunction, size);
  if (version >= 7) drawVersion(setFunction, version, size);

  const bits: number[] = [];
  for (const byte of codewords) appendBits(bits, byte, 8);
  let bitIndex = 0;
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vertical = 0; vertical < size; vertical += 1) {
      const y = upward ? size - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const x = right - offset;
        if (reserved[y][x]) continue;
        const bit = bitIndex < bits.length ? bits[bitIndex] === 1 : false;
        const masked = bit !== ((x + y) % 2 === 0);
        modules[y][x] = masked;
        bitIndex += 1;
      }
    }
    upward = !upward;
  }

  drawFormat(setFunction, 0, size);
  return modules;
}

function drawFinder(
  setFunction: (x: number, y: number, dark: boolean) => void,
  centerX: number,
  centerY: number,
): void {
  for (let dy = -4; dy <= 4; dy += 1) {
    for (let dx = -4; dx <= 4; dx += 1) {
      const distance = Math.max(Math.abs(dx), Math.abs(dy));
      const dark = distance <= 3 && distance !== 2;
      setFunction(centerX + dx, centerY + dy, dark);
    }
  }
}

function reserveFormat(
  setFunction: (x: number, y: number, dark: boolean) => void,
  size: number,
): void {
  for (let i = 0; i <= 5; i += 1) setFunction(8, i, false);
  setFunction(8, 7, false);
  setFunction(8, 8, false);
  setFunction(7, 8, false);
  for (let i = 9; i <= 14; i += 1) setFunction(14 - i, 8, false);

  for (let i = 0; i <= 7; i += 1) setFunction(size - 1 - i, 8, false);
  for (let i = 8; i <= 14; i += 1) setFunction(8, size - 15 + i, false);
  setFunction(8, size - 8, true);
}

function drawFormat(
  setFunction: (x: number, y: number, dark: boolean) => void,
  mask: number,
  size: number,
): void {
  const data = (1 << 3) | mask;
  let remainder = data;
  for (let index = 0; index < 10; index += 1) {
    remainder = (remainder << 1) ^ ((remainder >>> 9) * 0x537);
  }
  const bits = ((data << 10) | remainder) ^ 0x5412;
  const get = (index: number) => ((bits >>> index) & 1) !== 0;

  for (let i = 0; i <= 5; i += 1) setFunction(8, i, get(i));
  setFunction(8, 7, get(6));
  setFunction(8, 8, get(7));
  setFunction(7, 8, get(8));
  for (let i = 9; i <= 14; i += 1) setFunction(14 - i, 8, get(i));

  for (let i = 0; i <= 7; i += 1) setFunction(size - 1 - i, 8, get(i));
  for (let i = 8; i <= 14; i += 1) setFunction(8, size - 15 + i, get(i));
  setFunction(8, size - 8, true);
}

function drawVersion(
  setFunction: (x: number, y: number, dark: boolean) => void,
  version: number,
  size: number,
): void {
  let remainder = version;
  for (let index = 0; index < 12; index += 1) {
    remainder = (remainder << 1) ^ ((remainder >>> 11) * 0x1f25);
  }
  const bits = (version << 12) | remainder;
  for (let index = 0; index < 18; index += 1) {
    const dark = ((bits >>> index) & 1) !== 0;
    const a = size - 11 + (index % 3);
    const b = Math.floor(index / 3);
    setFunction(a, b, dark);
    setFunction(b, a, dark);
  }
}

function appendBits(target: number[], value: number, length: number): void {
  for (let shift = length - 1; shift >= 0; shift -= 1) {
    target.push((value >>> shift) & 1);
  }
}
