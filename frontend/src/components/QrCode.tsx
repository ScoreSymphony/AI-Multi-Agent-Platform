import { useMemo } from "react";

interface QrCodeProps {
  value: string;
  label?: string;
  size?: number;
}

interface QrVersionConfig {
  version: number;
  dataCodewords: number;
  eccPerBlock: number;
  blockDataSizes: number[];
  alignment: number[];
}

const CONFIGS: QrVersionConfig[] = [
  { version: 1, dataCodewords: 19, eccPerBlock: 7, blockDataSizes: [19], alignment: [] },
  { version: 2, dataCodewords: 34, eccPerBlock: 10, blockDataSizes: [34], alignment: [6, 18] },
  { version: 3, dataCodewords: 55, eccPerBlock: 15, blockDataSizes: [55], alignment: [6, 22] },
  { version: 4, dataCodewords: 80, eccPerBlock: 20, blockDataSizes: [80], alignment: [6, 26] },
  { version: 5, dataCodewords: 108, eccPerBlock: 26, blockDataSizes: [108], alignment: [6, 30] },
  { version: 6, dataCodewords: 136, eccPerBlock: 18, blockDataSizes: [68, 68], alignment: [6, 34] },
  { version: 7, dataCodewords: 156, eccPerBlock: 20, blockDataSizes: [78, 78], alignment: [6, 22, 38] },
  { version: 8, dataCodewords: 194, eccPerBlock: 24, blockDataSizes: [97, 97], alignment: [6, 24, 42] },
  { version: 9, dataCodewords: 232, eccPerBlock: 30, blockDataSizes: [116, 116], alignment: [6, 26, 46] },
  { version: 10, dataCodewords: 274, eccPerBlock: 18, blockDataSizes: [68, 68, 69, 69], alignment: [6, 28, 50] },
  { version: 11, dataCodewords: 324, eccPerBlock: 20, blockDataSizes: [81, 81, 81, 81], alignment: [6, 30, 54] },
  { version: 12, dataCodewords: 370, eccPerBlock: 24, blockDataSizes: [92, 92, 93, 93], alignment: [6, 32, 58] },
  { version: 13, dataCodewords: 428, eccPerBlock: 26, blockDataSizes: [107, 107, 107, 107], alignment: [6, 34, 62] },
];

const QUIET_ZONE = 4;

export function QrCode({ value, label = "Mobile pairing QR code", size = 220 }: QrCodeProps) {
  const matrix = useMemo(() => encodeQr(value), [value]);
  const dimension = matrix.length + QUIET_ZONE * 2;
  const path = useMemo(() => {
    const commands: string[] = [];
    for (let y = 0; y < matrix.length; y += 1) {
      for (let x = 0; x < matrix.length; x += 1) {
        if (matrix[y]?.[x]) {
          commands.push(`M${x + QUIET_ZONE},${y + QUIET_ZONE}h1v1h-1z`);
        }
      }
    }
    return commands.join("");
  }, [matrix]);

  return (
    <svg
      aria-label={label}
      height={size}
      role="img"
      shapeRendering="crispEdges"
      viewBox={`0 0 ${dimension} ${dimension}`}
      width={size}
    >
      <rect fill="white" height={dimension} width={dimension} x="0" y="0" />
      <path d={path} fill="black" />
    </svg>
  );
}

export function encodeQr(value: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(value));
  const config = CONFIGS.find((candidate) => fitsBytePayload(bytes.length, candidate));
  if (!config) {
    throw new Error("Pairing QR payload exceeds supported local QR capacity");
  }

  const data = makeDataCodewords(bytes, config);
  const codewords = addErrorCorrectionAndInterleave(data, config);
  const size = config.version * 4 + 17;
  const modules = Array.from({ length: size }, () => Array<boolean>(size).fill(false));
  const functionModules = Array.from({ length: size }, () => Array<boolean>(size).fill(false));

  const setFunction = (x: number, y: number, dark: boolean) => {
    modules[y]![x] = dark;
    functionModules[y]![x] = true;
  };

  drawFinder(3, 3, size, setFunction);
  drawFinder(size - 4, 3, size, setFunction);
  drawFinder(3, size - 4, size, setFunction);

  for (let i = 8; i < size - 8; i += 1) {
    setFunction(6, i, i % 2 === 0);
    setFunction(i, 6, i % 2 === 0);
  }

  for (const y of config.alignment) {
    for (const x of config.alignment) {
      if (functionModules[y]?.[x]) continue;
      drawAlignment(x, y, setFunction);
    }
  }

  drawFormatBits(size, 0, setFunction);
  if (config.version >= 7) drawVersionBits(config.version, size, setFunction);

  const bits = codewords.flatMap((value) =>
    Array.from({ length: 8 }, (_, index) => ((value >>> (7 - index)) & 1) !== 0),
  );
  drawCodewords(modules, functionModules, bits);
  applyMaskZero(modules, functionModules);
  return modules;
}

function fitsBytePayload(length: number, config: QrVersionConfig): boolean {
  const countBits = config.version <= 9 ? 8 : 16;
  return 4 + countBits + length * 8 <= config.dataCodewords * 8;
}

function makeDataCodewords(bytes: number[], config: QrVersionConfig): number[] {
  const capacity = config.dataCodewords * 8;
  const bits: number[] = [];
  appendBits(bits, 0b0100, 4);
  appendBits(bits, bytes.length, config.version <= 9 ? 8 : 16);
  for (const byte of bytes) appendBits(bits, byte, 8);
  const terminator = Math.min(4, capacity - bits.length);
  appendBits(bits, 0, terminator);
  while (bits.length % 8 !== 0) bits.push(0);

  const data: number[] = [];
  for (let index = 0; index < bits.length; index += 8) {
    let value = 0;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value << 1) | (bits[index + bit] ?? 0);
    }
    data.push(value);
  }
  for (let pad = 0; data.length < config.dataCodewords; pad += 1) {
    data.push(pad % 2 === 0 ? 0xec : 0x11);
  }
  return data;
}

function addErrorCorrectionAndInterleave(
  data: number[],
  config: QrVersionConfig,
): number[] {
  const divisor = reedSolomonDivisor(config.eccPerBlock);
  const dataBlocks: number[][] = [];
  const eccBlocks: number[][] = [];
  let offset = 0;
  for (const blockSize of config.blockDataSizes) {
    const block = data.slice(offset, offset + blockSize);
    offset += blockSize;
    dataBlocks.push(block);
    eccBlocks.push(reedSolomonRemainder(block, divisor));
  }

  const result: number[] = [];
  const longest = Math.max(...config.blockDataSizes);
  for (let index = 0; index < longest; index += 1) {
    for (const block of dataBlocks) {
      if (index < block.length) result.push(block[index]!);
    }
  }
  for (let index = 0; index < config.eccPerBlock; index += 1) {
    for (const block of eccBlocks) result.push(block[index]!);
  }
  return result;
}

function reedSolomonDivisor(degree: number): number[] {
  const result = Array<number>(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i += 1) {
    for (let j = 0; j < degree; j += 1) {
      result[j] = gfMultiply(result[j]!, root);
      if (j + 1 < degree) result[j] ^= result[j + 1]!;
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function reedSolomonRemainder(data: number[], divisor: number[]): number[] {
  const result = Array<number>(divisor.length).fill(0);
  for (const byte of data) {
    const factor = byte ^ result[0]!;
    result.shift();
    result.push(0);
    for (let i = 0; i < result.length; i += 1) {
      result[i] ^= gfMultiply(divisor[i]!, factor);
    }
  }
  return result;
}

function gfMultiply(left: number, right: number): number {
  let x = left;
  let y = right;
  let result = 0;
  for (let i = 0; i < 8; i += 1) {
    if ((y & 1) !== 0) result ^= x;
    const carry = (x & 0x80) !== 0;
    x = (x << 1) & 0xff;
    if (carry) x ^= 0x1d;
    y >>>= 1;
  }
  return result;
}

function drawFinder(
  centerX: number,
  centerY: number,
  size: number,
  setFunction: (x: number, y: number, dark: boolean) => void,
) {
  for (let dy = -4; dy <= 4; dy += 1) {
    for (let dx = -4; dx <= 4; dx += 1) {
      const x = centerX + dx;
      const y = centerY + dy;
      if (x < 0 || y < 0 || x >= size || y >= size) continue;
      const distance = Math.max(Math.abs(dx), Math.abs(dy));
      setFunction(x, y, distance !== 2 && distance !== 4);
    }
  }
}

function drawAlignment(
  centerX: number,
  centerY: number,
  setFunction: (x: number, y: number, dark: boolean) => void,
) {
  for (let dy = -2; dy <= 2; dy += 1) {
    for (let dx = -2; dx <= 2; dx += 1) {
      const distance = Math.max(Math.abs(dx), Math.abs(dy));
      setFunction(centerX + dx, centerY + dy, distance !== 1);
    }
  }
}

function drawFormatBits(
  size: number,
  mask: number,
  setFunction: (x: number, y: number, dark: boolean) => void,
) {
  const data = (1 << 3) | mask;
  let remainder = data;
  for (let i = 0; i < 10; i += 1) {
    remainder = (remainder << 1) ^ (((remainder >>> 9) & 1) * 0x537);
  }
  const bits = ((data << 10) | remainder) ^ 0x5412;
  const bit = (index: number) => ((bits >>> index) & 1) !== 0;

  for (let i = 0; i <= 5; i += 1) setFunction(8, i, bit(i));
  setFunction(8, 7, bit(6));
  setFunction(8, 8, bit(7));
  setFunction(7, 8, bit(8));
  for (let i = 9; i < 15; i += 1) setFunction(14 - i, 8, bit(i));

  for (let i = 0; i < 8; i += 1) setFunction(size - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i += 1) setFunction(8, size - 15 + i, bit(i));
  setFunction(8, size - 8, true);
}

function drawVersionBits(
  version: number,
  size: number,
  setFunction: (x: number, y: number, dark: boolean) => void,
) {
  let remainder = version;
  for (let i = 0; i < 12; i += 1) {
    remainder = (remainder << 1) ^ (((remainder >>> 11) & 1) * 0x1f25);
  }
  const bits = (version << 12) | remainder;
  for (let i = 0; i < 18; i += 1) {
    const dark = ((bits >>> i) & 1) !== 0;
    const a = size - 11 + (i % 3);
    const b = Math.floor(i / 3);
    setFunction(a, b, dark);
    setFunction(b, a, dark);
  }
}

function drawCodewords(
  modules: boolean[][],
  functionModules: boolean[][],
  bits: boolean[],
) {
  const size = modules.length;
  let bitIndex = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vertical = 0; vertical < size; vertical += 1) {
      const upward = ((right + 1) & 2) === 0;
      const y = upward ? size - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const x = right - offset;
        if (functionModules[y]?.[x]) continue;
        modules[y]![x] = bits[bitIndex] ?? false;
        bitIndex += 1;
      }
    }
  }
}

function applyMaskZero(modules: boolean[][], functionModules: boolean[][]) {
  for (let y = 0; y < modules.length; y += 1) {
    for (let x = 0; x < modules.length; x += 1) {
      if (!functionModules[y]?.[x] && (x + y) % 2 === 0) {
        modules[y]![x] = !modules[y]![x];
      }
    }
  }
}

function appendBits(target: number[], value: number, length: number) {
  for (let i = length - 1; i >= 0; i -= 1) {
    target.push((value >>> i) & 1);
  }
}
