import { NgTemplateOutlet } from '@angular/common';
import {
  Component,
  ElementRef,
  Injector,
  OnChanges,
  afterNextRender,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { Field, FieldEdit } from './api';

interface Row {
  id: number;
  source: number;
  value: string;
}

@Component({
  selector: 'app-metadata-field',
  imports: [NgTemplateOutlet],
  templateUrl: './metadata-field.html',
  styleUrl: './metadata-field.css',
})
export class MetadataField implements OnChanges {
  readonly field = input.required<Field>();
  readonly disabled = input(false);
  readonly id = input.required<string>();
  readonly save = output<FieldEdit>();
  readonly rows = signal<Row[]>([]);
  readonly editing = signal(false);
  private nextId = 0;
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  ngOnChanges() {
    if (!this.editing()) this.reset();
  }

  start() {
    this.reset();
    this.editing.set(true);
    afterNextRender(
      () => this.host.nativeElement.querySelector<HTMLElement>('textarea, select')?.focus(),
      { injector: this.injector },
    );
  }

  cancel() {
    this.editing.set(false);
    this.reset();
  }

  add() {
    this.rows.update((rows) => [...rows, this.row('', -1)]);
  }

  remove(index: number) {
    this.rows.update((rows) => rows.filter((_, position) => index !== position));
  }

  move(index: number, offset: number) {
    const rows = [...this.rows()];
    const target = index + offset;
    if (target < 0 || target >= rows.length) return;
    [rows[index], rows[target]] = [rows[target], rows[index]];
    this.rows.set(rows);
  }

  update(index: number, value: string) {
    this.rows.update((rows) =>
      rows.map((row, position) => (index === position ? { ...row, value } : row)),
    );
  }

  applyWhenFocusLeaves(event: FocusEvent) {
    if (!this.editing()) return;
    const next = event.relatedTarget;
    if (next instanceof Node && this.host.nativeElement.contains(next)) return;
    this.submit();
  }

  handleKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    this.cancel();
  }

  submit() {
    if (this.disabled()) return;
    const rows = this.rows();
    const field = this.field();
    const values = rows.map((row) => row.value);
    const original = field.kind === 'text' && !field.values.length ? [''] : field.values;
    if (
      values.length === original.length &&
      values.every((value, index) => value === original[index]) &&
      (field.kind !== 'repeated' || rows.every((row, index) => row.source === index))
    ) {
      this.cancel();
      return;
    }
    this.save.emit({
      tag: field.tag,
      values,
      ...(field.kind === 'repeated'
        ? { source_indices: rows.map((row) => row.source) }
        : {}),
    });
  }

  private reset() {
    const field = this.field();
    const values = field.kind === 'text' && !field.values.length ? [''] : field.values;
    this.rows.set(values.map((value, index) => this.row(value, index)));
  }

  private row(value: string, source: number): Row {
    return { id: this.nextId++, source, value };
  }
}
