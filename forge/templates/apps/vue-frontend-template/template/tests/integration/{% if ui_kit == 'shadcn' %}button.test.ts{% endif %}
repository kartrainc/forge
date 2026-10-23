import { expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import Button from '../../src/shared/ui/button/Button.vue';
it('composes variants, primitive rendering and click events', async () => {
  const button = mount(Button, { props: { variant: 'outline' }, slots: { default: 'Save' } });
  expect(button.element.tagName).toBe('BUTTON');
  expect(button.text()).toBe('Save');
  await button.trigger('click');
  expect(button.emitted('click')).toHaveLength(1);
  button.unmount();
});
