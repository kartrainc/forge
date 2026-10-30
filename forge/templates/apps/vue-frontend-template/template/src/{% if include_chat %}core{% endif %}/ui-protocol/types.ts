export type RenderEngine = 'ag-ui' | 'mcp-ext'

export interface AgUiPayload {
  engine: 'ag-ui'
  component_name: string
  props: Record<string, any>
}

export interface McpExtPayload {
  engine: 'mcp-ext'
  entryUrl?: string
  html?: string
  initialContext: Record<string, any>
  csp?: { connectDomains?: string[]; resourceDomains?: string[] }
  permissions?: Record<string, Record<string, never>>
}

export type UIResponse = AgUiPayload | McpExtPayload
