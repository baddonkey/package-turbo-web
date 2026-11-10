import { TestBed } from '@angular/core/testing';
import { App } from './app';
import { ContextMenu, ContextMenuState } from './context-menu';
import {
  Field,
  FieldEdit,
  MetadataEdit,
  NodeDetail,
  PackageAction,
  Sip,
  SipApi,
  SipNode,
} from './api';

const sip: Sip = {
  id: 'f044321a-b150-4a3d-9b40-af2f156ea558',
  name: 'SIP_20260101_Testamt',
  read_only: false,
  delivery_type: 'GEVER',
  schema_version: '5.1',
  revision: 'first',
};

function entity(id: string, kind: string, name: string, hasChildren = false): SipNode {
  return {
    id,
    kind,
    name,
    label: kind,
    has_children: hasChildren,
    appearance: '',
    reference: '',
    timespan: '',
  };
}

function field(tag: string, label: string, value: string): Field {
  return {
    tag,
    label,
    kind: 'text',
    values: [value],
    editable: true,
    primary: true,
    min_items: 0,
    max_items: 1,
    choices: [],
  };
}

const rootNode = entity('0', 'paket', sip.name, true);
const toc = entity('0.0', 'inhaltsverzeichnis', 'Paketinhalt', true);
const delivery = entity('0.1', 'ablieferung', 'Testamt', true);
const provenance = entity('0.1.0', 'provenienz', 'Testamt');
const system = entity('0.1.1', 'ordnungssystem', 'Testplan', true);
const position = entity('0.1.1.0', 'ordnungssystemposition', '1 Allgemeines', true);
const dossier = {
  ...entity('0.1.1.0.0', 'dossier', 'Sitzungsakten'),
  reference: '1.2',
  timespan: '2010 - 2012',
};
const content = entity('0.0.0', 'ordner', 'content');

describe('Shared item context menu', () => {
  it('focuses enabled commands, supports keyboard navigation and returns focus on Escape', async () => {
    await TestBed.configureTestingModule({ imports: [ContextMenu] }).compileComponents();
    const fixture = TestBed.createComponent(ContextMenu);
    const invoker = document.createElement('button');
    document.body.append(invoker);
    const menu: ContextMenuState = {
      x: 30,
      y: 40,
      label: 'Dossier',
      invoker,
      commands: [
        { label: 'Disabled', disabled: true, run: () => {} },
        { label: 'Show', run: () => {} },
        { label: 'Copy', run: () => {} },
      ],
    };
    fixture.componentRef.setInput('state', menu);
    await fixture.whenStable();
    const buttons = fixture.nativeElement.querySelectorAll(
      'button',
    ) as NodeListOf<HTMLButtonElement>;
    expect(document.activeElement).toBe(buttons[1]);
    buttons[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(document.activeElement).toBe(buttons[2]);
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => {
      closed = true;
    });
    buttons[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(closed).toBe(true);
    expect(document.activeElement).toBe(invoker);
    invoker.remove();
  });
});

const children: Record<string, SipNode[]> = {
  '0': [toc, delivery],
  [toc.id]: [content],
  [delivery.id]: [provenance, system],
  [system.id]: [position],
  [position.id]: [dossier],
};
const nodes = new Map(
  [rootNode, toc, delivery, provenance, system, position, dossier, content].map((node) => [
    node.id,
    node,
  ]),
);
const fields: Record<string, Field[]> = {
  [delivery.id]: [field('ablieferndeStelle', 'Abliefernde Stelle', 'Testamt')],
  [provenance.id]: [field('aktenbildnerName', 'Aktenbildner Name', 'Testarchiv')],
  [dossier.id]: [field('titel', 'Titel', 'Sitzungsakten')],
};

function detail(id: string): NodeDetail {
  return {
    node: nodes.get(id)!,
    parent_id: id === '0' ? null : id.slice(0, id.lastIndexOf('.')),
    fields: fields[id] ?? [],
    references: [],
    allowed_children:
      id === system.id ? ['ordnungssystemposition'] : id === position.id ? ['dossier'] : [],
    can_upload_file: false,
    can_add_note: id === dossier.id,
    file_name: null,
    revision: sip.revision,
  };
}

describe('Package Turbo desktop-style workspace', () => {
  const edits: { id: string; command: FieldEdit }[] = [];
  const actions: PackageAction[] = [];

  async function setup() {
    edits.length = 0;
    actions.length = 0;
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        {
          provide: SipApi,
          useValue: {
            list: async () => [sip],
            get: async () => sip,
            node: async (_sip: Sip, id: string) => detail(id),
            children: async (_sip: Sip, id: string) => ({
              items: children[id] ?? [],
              next_offset: null,
              revision: sip.revision,
            }),
            dossiers: async () => ({ items: [dossier], next_offset: null, revision: sip.revision }),
            exportUrl: () => '/api/v1/packages/test/export',
            metadataUrl: () => '/api/v1/packages/test/metadata.xml',
            reportUrl: () => '/api/v1/packages/test/reports/dossiers',
            contentUrl: () => '/api/v1/packages/test/nodes/file/content',
            notes: async () => ({ items: [], next_offset: null }),
            packageAction: async (_sip: Sip, action: PackageAction) => {
              actions.push(action);
              return { ...sip, revision: 'second' };
            },
            previewMetadata: async (_sip: Sip, item: MetadataEdit) => ({
              changed: true,
              field: field(item.edit.tag, 'Aktenbildner Name', item.edit.values[0]),
            }),
            commitMetadata: async (_sip: Sip, items: MetadataEdit[]) => ({
              revision: 'second',
              nodes: items.map(({ node_id: id, edit: command }) => {
                edits.push({ id, command });
                return {
                  ...detail(id),
                  revision: 'second',
                  fields: [field(command.tag, 'Aktenbildner Name', command.values[0])],
                };
              }),
            }),
            search: async () => ({
              revision: 'second',
              hits: [
                {
                  node_id: dossier.id,
                  object: 'Dossier',
                  name: dossier.name,
                  field: 'Titel',
                  value: dossier.name,
                },
              ],
            }),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const root: HTMLElement = fixture.nativeElement;
    const dialog = root.querySelector('dialog')!;
    Object.assign(dialog, {
      showModal: () => {
        dialog.open = true;
      },
      close: () => {
        dialog.open = false;
      },
    });
    return { fixture, root, dialog };
  }

  it('right-clicks an unselected classification item and executes against that item', async () => {
    const { fixture, root } = await setup();
    fixture.componentInstance.select(sip.id);
    await fixture.whenStable();
    fixture.componentInstance.navigate(dossier.id);
    await fixture.whenStable();
    const button = [
      ...root.querySelectorAll<HTMLButtonElement>('.classification button.node'),
    ].find((item) => item.title === position.name)!;
    const click = new MouseEvent('contextmenu', {
      bubbles: true,
      cancelable: true,
      clientX: 140,
      clientY: 180,
    });
    button.dispatchEvent(click);
    await fixture.whenStable();
    expect(click.defaultPrevented).toBe(true);
    expect(fixture.componentInstance.node()?.node.id).toBe(position.id);
    const menu = root.querySelector('[role="menu"]')!;
    expect(menu.getAttribute('aria-label')).toBe(position.name);
    const move = [...menu.querySelectorAll<HTMLButtonElement>('button')].find((item) =>
      item.textContent?.includes('Nach unten verschieben'),
    )!;
    move.click();
    await fixture.whenStable();
    expect(actions[0].node_id).toBe(position.id);
    expect(actions[0].action).toBe('move-down');
    expect(root.querySelector('[role="menu"]')).toBeNull();
  });

  it('opens dossier menus by keyboard and keeps their commands distinct', async () => {
    const { fixture, root } = await setup();
    fixture.componentInstance.select(sip.id);
    await fixture.whenStable();
    const button = root.querySelector<HTMLButtonElement>('.lower-navigator button.node')!;
    button.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'F10', shiftKey: true, bubbles: true, cancelable: true }),
    );
    await fixture.whenStable();
    const menu = root.querySelector('[role="menu"]')!;
    expect(menu.textContent).toContain('Neue Notiz');
    expect(menu.textContent).toContain('Im Ordnungssystem anzeigen');
    expect(menu.textContent).not.toContain('Objekt löschen');
  });

  it('offers bulk creation and disables writes for viewer packages', async () => {
    const { fixture, root, dialog } = await setup();
    fixture.componentInstance.select(sip.id);
    await fixture.whenStable();
    let button = [...root.querySelectorAll<HTMLButtonElement>('.classification button.node')].find(
      (item) => item.title === position.name,
    )!;
    button.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    const bulk = [...root.querySelectorAll<HTMLButtonElement>('[role="menu"] button')].find(
      (item) => item.textContent?.includes('Mehrere neue Dossier'),
    )!;
    bulk.click();
    await fixture.whenStable();
    expect(dialog.open).toBe(true);
    expect(fixture.componentInstance.childKind).toBe('dossier');
    expect(fixture.componentInstance.childCount).toBe(2);
    fixture.componentInstance.closeDialog();
    fixture.componentInstance.sip.set({ ...sip, role: 'viewer' });
    await fixture.whenStable();
    button = [...root.querySelectorAll<HTMLButtonElement>('.classification button.node')].find(
      (item) => item.title === position.name,
    )!;
    button.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    const commands = [...root.querySelectorAll<HTMLButtonElement>('[role="menu"] button')];
    expect(
      commands.find((item) => item.textContent?.includes('Mehrere neue Dossier'))?.disabled,
    ).toBe(true);
    expect(
      commands.some((item) => !item.disabled && item.textContent?.includes('Objekt löschen')),
    ).toBe(false);
  });

  it('opens note editing for the clicked note and keeps native text menus untouched', async () => {
    const { fixture, root, dialog } = await setup();
    fixture.componentInstance.select(sip.id);
    await fixture.whenStable();
    fixture.componentInstance.notes.set({
      items: [
        {
          node_id: 'note-id',
          owner_id: dossier.id,
          object_name: dossier.name,
          object_kind: 'Dossier',
          text: 'Original',
          created: '',
          author: 'Archivist',
        },
      ],
      next_offset: null,
    });
    fixture.componentInstance.setTool('notes');
    await fixture.whenStable();
    root
      .querySelector('.results tbody td')!
      .dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    [...root.querySelectorAll<HTMLButtonElement>('[role="menu"] button')]
      .find((item) => item.textContent?.includes('Notiz bearbeiten'))!
      .click();
    await fixture.whenStable();
    expect(dialog.open).toBe(true);
    const textarea = dialog.querySelector<HTMLTextAreaElement>('textarea')!;
    expect(textarea.value).toBe('Original');
    const native = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    textarea.dispatchEvent(native);
    expect(native.defaultPrevented).toBe(false);
    textarea.value = 'Edited';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    expect(actions[0]).toMatchObject({ action: 'edit-note', node_id: 'note-id', value: 'Edited' });
  });

  it('shows checkable column menus, hides a column and can restore it', async () => {
    const { fixture, root } = await setup();
    const first = root.querySelector<HTMLTableCellElement>('.results thead th')!;
    first.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    const toggle = root.querySelector<HTMLButtonElement>('[role="menuitemcheckbox"]')!;
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    toggle.click();
    await fixture.whenStable();
    expect(first.hidden).toBe(true);
    root
      .querySelectorAll('.results thead th')[1]
      .dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    [...root.querySelectorAll<HTMLButtonElement>('[role="menu"] button')]
      .find((item) => item.textContent?.includes('Alle Spalten anzeigen'))!
      .click();
    await fixture.whenStable();
    expect(first.hidden).toBe(false);
  });

  it('exposes the empty changes pane from both the tab bar and the View menu', async () => {
    const { fixture, root } = await setup();
    const tab = [...root.querySelectorAll<HTMLButtonElement>('.tool-tabs button')].find(
      (button) => button.textContent?.trim() === 'Änderungen',
    )!;
    const view = [...root.querySelectorAll('nav.menubar details')].find(
      (menu) => menu.querySelector('summary')?.textContent === 'Ansicht',
    )!;
    const item = [...view.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent?.trim() === 'Änderungen',
    )!;
    expect(tab.getAttribute('aria-pressed')).toBe('false');
    item.click();
    await fixture.whenStable();
    expect(tab.getAttribute('aria-pressed')).toBe('true');
    expect(root.querySelector('.changes-toolbar')?.textContent).toContain('0 Änderungen');
  });

  it('opens the package in four panes and navigates without hiding the other panes', async () => {
    const { fixture, root, dialog } = await setup();
    expect(root.querySelector('.welcome h2')?.textContent).toBe('Kein SIP geöffnet');
    root.querySelector<HTMLButtonElement>('.welcome-actions button')!.click();
    await fixture.whenStable();
    expect(dialog.open).toBe(true);
    dialog.querySelector<HTMLButtonElement>('.saved-packages button')!.click();
    await fixture.whenStable();

    expect(dialog.open).toBe(false);
    expect(root.querySelector('.classification')?.textContent).toContain('1 Allgemeines');
    expect(root.querySelector('.lower-navigator')?.textContent).toContain('1.2');
    expect(root.querySelector('.editor')?.textContent).toContain('Abliefernde Stelle');
    expect(root.querySelector('.editor')?.textContent).toContain('Aktenbildner Name');
    expect(root.querySelector('.results')?.textContent).toContain('Validierungshilfe');

    root.querySelector<HTMLButtonElement>('.lower-navigator button.node')!.click();
    await fixture.whenStable();
    expect(root.querySelector('.editor h3')?.textContent).toContain('Sitzungsakten');
    expect(
      root.querySelector('.classification button.node[aria-current="true"]')?.textContent,
    ).toContain('Sitzungsakten');

    const fileTab = [...root.querySelectorAll<HTMLButtonElement>('.split-tabs button')].find(
      (button) => button.textContent?.includes('Paketinhalt'),
    )!;
    fileTab.click();
    await fixture.whenStable();
    expect(root.querySelector('.lower-navigator')?.textContent).toContain('content');
    expect(root.querySelector('.editor h3')?.textContent).toContain('Sitzungsakten');
  });

  it('saves the edited overview entity and keeps metadata visible during search', async () => {
    const { fixture, root } = await setup();
    fixture.componentInstance.select(sip.id);
    await fixture.whenStable();

    root.querySelector<HTMLButtonElement>('[aria-label="Aktenbildner Name bearbeiten"]')!.click();
    await fixture.whenStable();
    let input = root.querySelector<HTMLTextAreaElement>('app-metadata-field textarea')!;
    expect(document.activeElement).toBe(input);
    input.value = 'Verwerfen';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await fixture.whenStable();
    expect(root.querySelector('app-metadata-field textarea')).toBeNull();
    expect(edits).toEqual([]);

    root.querySelector<HTMLButtonElement>('[aria-label="Aktenbildner Name bearbeiten"]')!.click();
    await fixture.whenStable();
    input = root.querySelector<HTMLTextAreaElement>('app-metadata-field textarea')!;
    expect(document.activeElement).toBe(input);
    input.value = 'Neues Archiv';
    input.dispatchEvent(new Event('input'));
    const save = [...root.querySelectorAll<HTMLButtonElement>('.toolbar button')].find(
      (button) => button.textContent?.trim() === 'Speichern',
    )!;
    expect(save.disabled).toBe(true);
    expect(root.querySelector('app-metadata-field button[type="submit"]')).toBeNull();
    input.dispatchEvent(
      new FocusEvent('focusout', { bubbles: true, relatedTarget: document.body }),
    );
    await fixture.whenStable();
    expect(root.querySelector('.changes-table tbody')?.textContent).toContain('Testarchiv');
    expect(root.querySelector('.changes-table tbody')?.textContent).toContain('Neues Archiv');
    expect(edits).toEqual([]);
    expect(save.disabled).toBe(false);
    save.click();
    await fixture.whenStable();
    expect(edits).toEqual([
      { id: provenance.id, command: { tag: 'aktenbildnerName', values: ['Neues Archiv'] } },
    ]);
    expect(root.querySelector('.sip-summary')?.textContent).toContain('Neues Archiv');
    expect(root.querySelector('.changes-table tbody tr')).toBeNull();

    fixture.componentInstance.query = dossier.name;
    fixture.componentInstance.search();
    await fixture.whenStable();
    expect(root.querySelector('.results tbody')?.textContent).toContain(dossier.name);
    expect(root.querySelector('.editor')?.textContent).toContain('Abliefernde Stelle');
  });
});
