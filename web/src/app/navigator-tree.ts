import { Component, input, output } from '@angular/core';
import type { SipNode } from './api';
import type { ItemContext } from './context-menu';

export type TreeRow =
  | {
      key: string;
      node: SipNode;
      depth: number;
      expandable: boolean;
      expanded: boolean;
      parentId?: string;
    }
  | { key: string; parent: string; depth: number };

const badges: Record<string, string> = {
  ordnungssystem: 'OS',
  ordnungssystemposition: 'OSP',
  dossier: 'D',
  subdossier: 'SD',
  dokument: 'Dk',
  mappe: 'M',
  vorgang: 'V',
  aktivitaet: 'A',
  paket: 'P',
  ordner: 'O',
  datei: 'F',
};

@Component({
  selector: 'app-navigator-tree',
  template: `
    @if (columns()) {
      <div class="tree-heading">
        <span>Titel</span><span>Aktenzeichen</span><span>Entstehungszeitraum</span
        ><span>Erscheinungsform</span>
      </div>
    }
    <ul [class.with-columns]="columns()" [attr.aria-label]="label()">
      @for (row of rows(); track row.key) {
        @if ('node' in row) {
          <li
            [class.selected]="selected() === row.node.id"
            (contextmenu)="openContext($event, row.node.id, row.parentId)"
          >
            <div class="tree-name" [style.padding-inline-start.px]="4 + row.depth * 20">
              @if (row.expandable) {
                <button
                  class="disclosure"
                  type="button"
                  [disabled]="disabled()"
                  [attr.aria-expanded]="row.expanded"
                  [attr.aria-label]="
                    (row.expanded ? 'Einklappen: ' : 'Aufklappen: ') + row.node.name
                  "
                  (click)="toggle.emit(row.node.id)"
                >
                  <span aria-hidden="true">{{ row.expanded ? '▾' : '▸' }}</span>
                </button>
              } @else {
                <span class="disclosure" aria-hidden="true"></span>
              }
              <button
                class="node"
                type="button"
                [disabled]="disabled()"
                [attr.title]="row.node.name"
                [attr.aria-current]="selected() === row.node.id ? 'true' : null"
                aria-haspopup="menu"
                (click)="select.emit(row.node.id)"
                (keydown)="contextKey($event, row.node.id, row.parentId)"
              >
                @if (badge(row.node.kind); as code) {
                  <span class="badge" [attr.data-kind]="row.node.kind" aria-hidden="true">{{
                    code
                  }}</span>
                }
                <span class="label">{{ row.node.name || row.node.label }}</span>
              </button>
              <button
                class="context-trigger"
                type="button"
                [disabled]="disabled()"
                [attr.aria-label]="row.node.name + ': Menü'"
                aria-haspopup="menu"
                [attr.title]="row.node.name + ': Menü'"
                (click)="openContext($event, row.node.id, row.parentId)"
              >
                <img src="/ellipsis-vertical.svg" alt="" />
              </button>
            </div>
            @if (columns()) {
              <span [title]="row.node.reference">{{ row.node.reference }}</span>
              <span [title]="row.node.timespan">{{ row.node.timespan }}</span>
              <span [title]="row.node.appearance">{{ row.node.appearance }}</span>
            }
          </li>
        } @else {
          <li class="more" [style.padding-inline-start.px]="24 + row.depth * 20">
            <button type="button" [disabled]="disabled()" (click)="more.emit(row.parent)">
              Weitere anzeigen…
            </button>
          </li>
        }
      }
    </ul>
  `,
  styleUrl: './navigator-tree.css',
})
export class NavigatorTree {
  readonly rows = input.required<TreeRow[]>();
  readonly label = input.required<string>();
  readonly columns = input(false);
  readonly selected = input<string>();
  readonly disabled = input(false);
  readonly select = output<string>();
  readonly toggle = output<string>();
  readonly more = output<string>();
  readonly itemContext = output<ItemContext>();

  openContext(event: MouseEvent | KeyboardEvent, nodeId: string, parentId?: string) {
    event.preventDefault();
    event.stopPropagation();
    if (!this.disabled()) this.itemContext.emit({ event, nodeId, parentId });
  }

  contextKey(event: KeyboardEvent, nodeId: string, parentId?: string) {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))
      this.openContext(event, nodeId, parentId);
  }

  badge(kind: string) {
    return badges[kind];
  }
}
