/**
 * 分栏拖动:把"侧栏宽度"夹进合法区间。
 *
 * 两侧各留最小宽度 —— 否则能把侧栏拖成 0(等于收起)或把消息区压没。
 * 容器太窄时优先保证消息区(侧栏退到最小宽度)。
 */
export const MIN_PANEL_WIDTH = 280
export const MIN_CHAT_WIDTH = 360

export function clampPanelWidth(
  panel: number,
  total: number,
  minPanel = MIN_PANEL_WIDTH,
  minChat = MIN_CHAT_WIDTH,
): number {
  const max = Math.max(minPanel, total - minChat)
  return Math.min(max, Math.max(minPanel, Math.round(panel)))
}
