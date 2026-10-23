import { afterEach, expect, it } from 'vitest'
import { defineComponent, ref } from 'vue'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import * as Dialog from '../../src/shared/ui/dialog'
import * as Select from '../../src/shared/ui/select'
import * as Sidebar from '../../src/shared/ui/sidebar'
import * as Table from '../../src/shared/ui/table'
import * as Toggle from '../../src/shared/ui/toggle-group'

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })

it('renders an accessible dialog through its portal and closes it', async () => {
  wrapper = mount(defineComponent({
    components: { ...Dialog },
    template: `<Dialog><DialogTrigger>Open preferences</DialogTrigger><DialogContent>
      <DialogHeader><DialogTitle>Preferences</DialogTitle><DialogDescription>Choose display options</DialogDescription></DialogHeader>
      <DialogFooter><DialogClose>Done</DialogClose></DialogFooter>
    </DialogContent></Dialog>`,
  }), { attachTo: document.body })
  await wrapper.get('button').trigger('click')
  await flushPromises()
  const dialog = document.querySelector('[role="dialog"]')!
  expect(dialog.textContent).toContain('Choose display options')
  expect(document.getElementById(dialog.getAttribute('aria-labelledby')!)?.textContent).toBe('Preferences')
  const done = Array.from(dialog.querySelectorAll('button')).find(button => button.textContent === 'Done')!
  done.click()
  await flushPromises()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
})

it('composes a select with labelled options and updates the bound value', async () => {
  const selected = ref('first')
  wrapper = mount(defineComponent({
    components: { ...Select }, setup: () => ({ selected }),
    template: `<Select v-model="selected" :open="true"><SelectTrigger><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup><SelectLabel>Priority</SelectLabel>
      <SelectItem value="first">First</SelectItem><SelectSeparator /><SelectItem value="second">Second</SelectItem>
      </SelectGroup></SelectContent></Select>`,
  }), { attachTo: document.body })
  await flushPromises()
  const options = Array.from(document.querySelectorAll('[role="option"]'))
  expect(options.map(option => option.textContent?.trim())).toEqual(['First', 'Second'])
  ;(options[1] as HTMLElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await flushPromises()
  expect(selected.value).toBe('second')
})

it('preserves sidebar state, table semantics and toggle selection when composed', async () => {
  const selected = ref('a')
  wrapper = mount(defineComponent({
    components: { ...Sidebar, ...Table, ...Toggle }, setup: () => ({ selected }),
    template: `<SidebarProvider :default-open="true"><Sidebar><SidebarHeader>Workspace</SidebarHeader><SidebarContent>
      <SidebarGroup><SidebarGroupLabel>Navigation</SidebarGroupLabel><SidebarGroupContent>
      <SidebarMenu><SidebarMenuItem><SidebarMenuButton>Overview</SidebarMenuButton></SidebarMenuItem></SidebarMenu>
      </SidebarGroupContent></SidebarGroup></SidebarContent><SidebarFooter>Account</SidebarFooter></Sidebar>
      <SidebarInset><SidebarTrigger /><Table><TableHeader><TableRow><TableHead>Name</TableHead></TableRow></TableHeader>
      <TableBody><TableRow><TableCell>Example</TableCell></TableRow></TableBody></Table>
      <ToggleGroup type="single" v-model="selected" v-slot="{ modelValue, update }"><ToggleGroupItem value="a" :model-value="modelValue" @click="update('a')">Alpha</ToggleGroupItem><ToggleGroupItem value="b" :model-value="modelValue" @click="update('b')">Beta</ToggleGroupItem></ToggleGroup>
      </SidebarInset></SidebarProvider>`,
  }))
  expect(wrapper.get('th').text()).toBe('Name')
  expect(wrapper.get('td').text()).toBe('Example')
  expect(wrapper.attributes('data-sidebar-state')).toBe('expanded')
  const trigger = wrapper.findAll('button').find(button => button.text() === 'Toggle Sidebar')!
  await trigger.trigger('click')
  expect(wrapper.attributes('data-sidebar-state')).toBe('collapsed')
  const beta = wrapper.findAll('button').find(button => button.text() === 'Beta')!
  await beta.trigger('click')
  expect(selected.value).toBe('b')
})
