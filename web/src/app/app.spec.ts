import { TestBed } from '@angular/core/testing';
import { App } from './app';
import { Field, FieldEdit, MetadataEdit, NodeDetail, Sip, SipApi, SipNode } from './api';

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
    allowed_children: [],
    can_upload_file: false,
    can_add_note: id === dossier.id,
    file_name: null,
    revision: sip.revision,
  };
}

describe('Package Turbo desktop-style workspace', () => {
  const edits: { id: string; command: FieldEdit }[] = [];

  async function setup() {
    edits.length = 0;
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
