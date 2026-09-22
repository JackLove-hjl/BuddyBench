import { fetchJson } from './client'

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface UserInfo {
  id: string
  username: string
  created_at: string
}

export function register(body: { username: string; password: string }): Promise<TokenResponse> {
  return fetchJson<TokenResponse>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) })
}

export function login(body: { username: string; password: string }): Promise<TokenResponse> {
  return fetchJson<TokenResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) })
}

export function fetchMe(): Promise<UserInfo> {
  return fetchJson<UserInfo>('/api/auth/me')
}
