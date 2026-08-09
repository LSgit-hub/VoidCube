import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import png2icons from 'png2icons'
import pngjs from 'pngjs'

const { PNG } = pngjs
const root = dirname(dirname(fileURLToPath(import.meta.url)))
const assetDirectory = join(root, 'assets')
const size = 1024
const png = new PNG({ width: size, height: size, colorType: 6 })

const palette = {
  background: [17, 24, 32, 255],
  edge: [116, 230, 196, 255]
}

function insideRoundedSquare(x, y) {
  const inset = 42
  const radius = 190
  const left = inset
  const right = size - inset
  const top = inset
  const bottom = size - inset
  const clampedX = Math.max(left + radius, Math.min(right - radius, x))
  const clampedY = Math.max(top + radius, Math.min(bottom - radius, y))
  return (x - clampedX) ** 2 + (y - clampedY) ** 2 <= radius ** 2
}

function distanceToSegment(x, y, start, end) {
  const [x1, y1] = start
  const [x2, y2] = end
  const lengthSquared = (x2 - x1) ** 2 + (y2 - y1) ** 2
  const amount = Math.max(0, Math.min(1, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / lengthSquared))
  return Math.hypot(x - (x1 + amount * (x2 - x1)), y - (y1 + amount * (y2 - y1)))
}

function writePixel(x, y, color) {
  const offset = (y * size + x) * 4
  png.data[offset] = color[0]
  png.data[offset + 1] = color[1]
  png.data[offset + 2] = color[2]
  png.data[offset + 3] = color[3]
}

const polylines = [
  [[430, 206], [889, 411], [594, 818], [135, 613], [430, 206]],
  [[660, 308], [364, 716]],
  [[282, 410], [742, 614]]
]

const edges = polylines.flatMap((polyline) => [
  ...polyline.slice(1).map((point, index) => [polyline[index], point])
])
const lineRadius = 18

function blend(background, foreground, amount) {
  const ratio = Math.max(0, Math.min(1, amount))
  return [
    Math.round(background[0] + (foreground[0] - background[0]) * ratio),
    Math.round(background[1] + (foreground[1] - background[1]) * ratio),
    Math.round(background[2] + (foreground[2] - background[2]) * ratio),
    Math.round(background[3] + (foreground[3] - background[3]) * ratio)
  ]
}

const segments = [
  ...edges
]

for (let y = 0; y < size; y += 1) {
  for (let x = 0; x < size; x += 1) {
    let color = insideRoundedSquare(x, y) ? palette.background : [0, 0, 0, 0]
    const distance = Math.min(...segments.map(([start, end]) => distanceToSegment(x, y, start, end)))
    if (distance <= lineRadius + 1) color = blend(color, palette.edge, lineRadius + 1 - distance)
    writePixel(x, y, color)
  }
}

await mkdir(assetDirectory, { recursive: true })
const pngPath = join(assetDirectory, 'icon.png')
await writeFile(pngPath, PNG.sync.write(png))
const source = await readFile(pngPath)
const ico = png2icons.createICO(source, png2icons.BICUBIC, 0, false, true)
const icns = png2icons.createICNS(source, png2icons.BICUBIC, 0)
if (!ico || !icns) throw new Error('Unable to generate desktop icon containers')
await writeFile(join(assetDirectory, 'icon.ico'), ico)
await writeFile(join(assetDirectory, 'icon.icns'), icns)
