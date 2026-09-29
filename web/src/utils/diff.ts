/**
 * 行级 diff(edit_file 的 old_string → new_string 展示成红/绿)。
 *
 * 只用于工具卡展示,不追求最小编辑脚本:标准 LCS 回溯,顺序为「相同 / 删除 / 新增」。
 * 行数过大时退化为"整块删 + 整块增",避免 O(n·m) 把界面卡住。
 */
export interface DiffLine {
  kind: 'same' | 'add' | 'del'
  text: string
}

const MAX_CELLS = 40000

export function lineDiff(oldText: string, newText: string): DiffLine[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  if (a.length * b.length > MAX_CELLS) {
    return [
      ...a.map((text) => ({ kind: 'del' as const, text })),
      ...b.map((text) => ({ kind: 'add' as const, text })),
    ]
  }
  // dp[i][j] = a[i..] 与 b[j..] 的最长公共子序列长度
  const dp: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  )
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'del', text: a[i] })
      i++
    } else {
      out.push({ kind: 'add', text: b[j] })
      j++
    }
  }
  while (i < a.length) out.push({ kind: 'del', text: a[i++] })
  while (j < b.length) out.push({ kind: 'add', text: b[j++] })
  return out
}

/**
 * 新建文件的 diff:整篇都是新增。
 *
 * 不能直接用 `lineDiff('', content)`:空串 split 出来是一个空行,会凭空多一条删除。
 */
export function newFileDiff(content: string): DiffLine[] {
  if (!content) return []
  // 末尾换行会 split 出一个空元素,当成一行新增会让统计多算一行
  const text = content.endsWith('\n') ? content.slice(0, -1) : content
  return text.split('\n').map((line) => ({ kind: 'add' as const, text: line }))
}

/** +新增 / −删除 行数统计(卡片头部的 +14 −1) */
export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const line of lines) {
    if (line.kind === 'add') added += 1
    else if (line.kind === 'del') removed += 1
  }
  return { added, removed }
}
