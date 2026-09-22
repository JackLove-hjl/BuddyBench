/** 斜杠命令:定义与过滤。
 *
 * 单独成文件是为了让「菜单渲染的列表」和「Enter 选中的那条」共用同一份过滤实现
 * —— 两处各写一遍很容易出现"看到的是 A、回车执行的是 B"的错位。
 */

export interface CommandDef {
  key: string
  label: string
  description: string
  run: () => void
}

/** 按 key / label 过滤命令;query 为空时返回全部 */
export function filterCommands(commands: CommandDef[], query: string): CommandDef[] {
  const q = query.trim().toLowerCase()
  if (!q) return commands
  return commands.filter(
    (c) => c.key.toLowerCase().includes(q) || c.label.toLowerCase().includes(q),
  )
}
