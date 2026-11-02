/**
 * What the router factory received from the project, for the shell, the menus and the
 * built-in pages: the authentication contract, the menu actions and the routing in force
 * (the configuration's, with the landing route of the layout mounted at `/`). Generated
 * by forge.
 */
import { application } from './config'
import type { AuthContract, MenuAction, RoutingKey } from './contracts'

interface ApplicationContext {
  auth: AuthContract
  actions: Record<string, MenuAction>
  /** The destinations in force; the configuration's when left out. */
  routing?: Partial<Record<RoutingKey, string>>
}

let current: ApplicationContext | null = null

export function setApplicationContext(context: ApplicationContext): void {
  current = context
}

function context(): ApplicationContext {
  if (!current) throw new Error('createApplicationRouter() has not run: the application has no context yet.')
  return current
}

/** The authentication contract the project passed to the router factory. */
export function useApplicationAuth(): AuthContract {
  return context().auth
}

/** The destinations the guards use, the landing route the mounted layouts start on among them. */
export function useApplicationRouting(): Partial<Record<RoutingKey, string>> {
  return current?.routing ?? application.routing
}

/** Run a menu action by its id; an id the project did not pass is an error. */
export function runAction(id: string): unknown {
  const action = context().actions[id]
  if (!action) throw new Error(`Menu action '${id}' was not passed to createApplicationRouter().`)
  return action()
}

export function hasAction(id: string): boolean {
  return Boolean(current?.actions[id])
}
