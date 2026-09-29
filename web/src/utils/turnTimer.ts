/**
 * 本轮对话用时:纯状态机 + 格式化(无框架依赖,便于单测)。
 *
 * 计时口径:从用户发出消息到本轮结束的墙钟时间,**扣除等待用户的那段时间**
 * (审批卡 / 计划评审 / ask_user 作答期间模型并没有在干活,而这常常是几分钟,
 * 算进去会让"本轮用时"失去参考意义)。最小展示单位是秒。
 */

export interface TurnTiming {
  /** 本轮开始时刻(Date.now()) */
  startedAt: number
  /** 累计"等待用户"的时长 */
  waitedMs: number
  /** 结束时刻;null = 本轮仍在进行 */
  endedAt: number | null
  /** 是否正等待用户(审批/提问);等待中的时间不计入 */
  waitingSince: number | null
}

export type TurnAction =
  /** 新一轮开始(用户发出消息) */
  | { type: 'begin'; at: number }
  /** 挂起等用户决策(审批卡/计划评审/提问) */
  | { type: 'wait'; at: number }
  /** 用户已决策,继续本轮 */
  | { type: 'resume'; at: number }
  /** 本轮结束(done/error/手动停止) */
  | { type: 'end'; at: number }
  /** 丢弃计时(请求根本没跑起来、切换/新建会话) */
  | { type: 'reset' }

/**
 * 推进计时状态机。
 *
 * 所有动作都做了幂等保护:重复的 wait / resume / end 不会重复累计时间 ——
 * SSE 事件重放、组件重复触发都不会把用时算错。
 */
export function reduceTurn(state: TurnTiming | null, action: TurnAction): TurnTiming | null {
  switch (action.type) {
    case 'begin':
      return { startedAt: action.at, waitedMs: 0, endedAt: null, waitingSince: null }
    case 'wait':
      if (!state || state.endedAt !== null || state.waitingSince !== null) return state
      return { ...state, waitingSince: action.at }
    case 'resume': {
      if (!state || state.waitingSince === null) return state
      return {
        ...state,
        waitedMs: state.waitedMs + Math.max(0, action.at - state.waitingSince),
        waitingSince: null,
        // 决策前若曾被标记结束(异常路径),继续本轮时重新开始计时
        endedAt: null,
      }
    }
    case 'end': {
      if (!state || state.endedAt !== null) return state
      // 在等待用户时结束(用户拒绝后模型收尾/停止):等待的那一段同样不计入
      const waitedMs =
        state.waitingSince === null
          ? state.waitedMs
          : state.waitedMs + Math.max(0, action.at - state.waitingSince)
      return { ...state, waitedMs, waitingSince: null, endedAt: action.at }
    }
    case 'reset':
      return null
  }
}

/**
 * 本轮已用时:进行中取 now,已结束取 endedAt;等待用户的那段不计入,
 * 因此等待期间数值会"冻住",用户决策后继续往上走。
 */
export function turnElapsedMs(state: TurnTiming | null, now: number): number {
  if (!state) return 0
  const end = state.endedAt ?? Math.min(now, state.waitingSince ?? now)
  return Math.max(0, end - state.startedAt - state.waitedMs)
}

/** 展示用时:最小单位为秒("57 秒" / "2 分 05 秒" / "1 小时 02 分 05 秒") */
export function formatTurnDuration(ms: number): string {
  const total = Math.floor(Math.max(0, ms) / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h} 小时 ${String(m).padStart(2, '0')} 分 ${String(s).padStart(2, '0')} 秒`
  if (m) return `${m} 分 ${String(s).padStart(2, '0')} 秒`
  return `${s} 秒`
}

/** 单步耗时:不足 1 秒的工具调用保留毫秒精度(展开明细时用) */
export function formatStepDuration(ms: number): string {
  const value = Math.max(0, ms)
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(1)} 秒`
}
