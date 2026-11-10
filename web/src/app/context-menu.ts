import {
  Component,
  DestroyRef,
  ElementRef,
  HostListener,
  Injector,
  afterNextRender,
  effect,
  inject,
  input,
  output,
} from '@angular/core';

export interface ContextCommand {
  label: string;
  run: () => void;
  disabled?: boolean;
  separator?: boolean;
  danger?: boolean;
  icon?: string;
  checked?: boolean;
}

export interface ContextMenuState {
  x: number;
  y: number;
  label: string;
  commands: ContextCommand[];
  invoker: HTMLElement;
}

export interface ItemContext {
  event: MouseEvent | KeyboardEvent;
  nodeId: string;
  parentId?: string;
}

@Component({
  selector: 'app-context-menu',
  template: `
    @if (state(); as menu) {
      <div
        class="context-menu"
        role="menu"
        [attr.aria-label]="menu.label"
        [style.left.px]="menu.x"
        [style.top.px]="menu.y"
        (keydown)="keydown($event)"
        (contextmenu)="$event.preventDefault()"
      >
        @for (command of menu.commands; track $index) {
          @if (command.separator) {
            <div role="separator" class="separator"></div>
          }
          <button
            type="button"
            [attr.role]="command.checked === undefined ? 'menuitem' : 'menuitemcheckbox'"
            [attr.aria-checked]="command.checked"
            tabindex="-1"
            [disabled]="command.disabled"
            [class.danger]="command.danger"
            (click)="execute(command)"
          >
            @if (command.checked !== undefined) {
              <input type="checkbox" tabindex="-1" aria-hidden="true" [checked]="command.checked" />
            } @else if (command.icon) {
              <img [src]="'/' + command.icon + '.svg'" alt="" />
            } @else {
              <span class="icon-space" aria-hidden="true"></span>
            }
            <span>{{ command.label }}</span>
          </button>
        }
      </div>
    }
  `,
  styles: `
    .context-menu {
      position: fixed;
      z-index: 1000;
      box-sizing: border-box;
      width: max-content;
      max-width: calc(100vw - 16px);
      max-height: calc(100dvh - 16px);
      overflow: auto;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--paper);
      color: inherit;
      box-shadow: 0 4px 16px #0002;
      font: inherit;
    }
    button {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      min-height: 32px;
      border: 0;
      border-radius: 2px;
      background: transparent;
      color: inherit;
      padding: 6px 12px;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }
    button span:last-child {
      white-space: normal;
      overflow-wrap: anywhere;
    }
    button:hover:not(:disabled),
    button:focus-visible {
      background: var(--table-head);
      outline: none;
    }
    button:disabled {
      opacity: 0.45;
      cursor: default;
    }
    button.danger {
      color: var(--danger);
    }
    img,
    .icon-space,
    input {
      flex: 0 0 16px;
      width: 16px;
      height: 16px;
    }
    input {
      pointer-events: none;
      min-height: 0;
      margin: 0;
    }
    .separator {
      border-top: 1px solid var(--line);
      margin: 4px 8px;
    }
  `,
})
export class ContextMenu {
  readonly state = input<ContextMenuState | null>(null);
  readonly closed = output<void>();
  private readonly element = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  constructor() {
    const onScroll = (event: Event) => {
      if (!this.element.nativeElement.contains(event.target as Node)) this.dismiss(false);
    };
    document.addEventListener('scroll', onScroll, true);
    inject(DestroyRef).onDestroy(() => document.removeEventListener('scroll', onScroll, true));
    effect(() => {
      const state = this.state();
      if (!state) return;
      afterNextRender(
        () => {
          if (this.state() !== state) return;
          const menu = this.element.nativeElement.querySelector<HTMLElement>('[role="menu"]');
          if (!menu) return;
          const bounds = menu.getBoundingClientRect();
          menu.style.left = `${Math.max(8, Math.min(state.x, window.innerWidth - bounds.width - 8))}px`;
          menu.style.top = `${Math.max(8, Math.min(state.y, window.innerHeight - bounds.height - 8))}px`;
          this.buttons()[0]?.focus();
        },
        { injector: this.injector },
      );
    });
  }

  private buttons() {
    return [
      ...this.element.nativeElement.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
    ];
  }

  execute(command: ContextCommand) {
    if (command.disabled) return;
    this.dismiss();
    command.run();
  }

  dismiss(restoreFocus = true) {
    if (!this.state()) return;
    if (restoreFocus) this.state()?.invoker.focus();
    this.closed.emit();
  }

  keydown(event: KeyboardEvent) {
    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
      }
      this.dismiss();
      return;
    }
    const buttons = this.buttons();
    if (!buttons.length) return;
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    let next: number;
    switch (event.key) {
      case 'ArrowDown':
        next = (current + 1) % buttons.length;
        break;
      case 'ArrowUp':
        next = (current - 1 + buttons.length) % buttons.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = buttons.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    buttons[next].focus();
  }

  @HostListener('document:pointerdown', ['$event'])
  outside(event: PointerEvent) {
    if (!this.element.nativeElement.contains(event.target as Node)) this.dismiss(false);
  }

  @HostListener('window:resize')
  resized() {
    this.dismiss();
  }
}
