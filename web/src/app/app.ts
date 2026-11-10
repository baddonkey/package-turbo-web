import { DatePipe, NgTemplateOutlet } from '@angular/common';
import {
  Component,
  ElementRef,
  Injector,
  HostListener,
  afterNextRender,
  afterEveryRender,
  computed,
  inject,
  OnInit,
  signal,
  viewChild,
  viewChildren,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { zip, type Zippable } from 'fflate';
import {
  ChildCreate,
  CleanupPreview,
  CreateSip,
  Field,
  FieldEdit,
  MetadataEdit,
  NameValue,
  History,
  NodeDetail,
  NodePage,
  NotesPage,
  PackageAction,
  SampleResult,
  SearchOption,
  SearchResult,
  Sip,
  SipApi,
  SipNode,
  Statistics,
  ValidationIssue,
  ValidationResult,
  errorMessage,
} from './api';
import { MetadataField } from './metadata-field';
import { NavigatorTree, TreeRow } from './navigator-tree';
import { ContextCommand, ContextMenu, ContextMenuState, ItemContext } from './context-menu';

type Tool = 'validation' | 'notes' | 'sample' | 'search' | 'changes' | 'history';
interface PendingEdit {
  request: MetadataEdit;
  field: Field;
  saved: Field;
  time: Date;
  object: SipNode;
}

type Editor = 'overview' | 'statistics' | 'validation' | 'object';
type Dialog =
  | 'open'
  | 'create'
  | 'child'
  | 'actions'
  | 'properties'
  | 'cleanup'
  | 'event'
  | 'note-edit'
  | 'content';
type Action = PackageAction['action'];
const DOSSIERS = 'dossiers';
const recordKinds = new Set([
  'dossier',
  'subdossier',
  'dokument',
  'mappe',
  'vorgang',
  'aktivitaet',
  'datei',
]);
const kindNames: Record<string, string> = {
  ordnungssystem: 'Ordnungssystem',
  ordnungssystemposition: 'Ordnungssystemposition',
  dossier: 'Dossier',
  subdossier: 'Subdossier',
  dokument: 'Dokument',
  vorgang: 'Vorgang',
  aktivitaet: 'Aktivität',
  mappe: 'Mappe',
  datei: 'Datei',
  ordner: 'Ordner',
};
const creationNames: Record<string, [string, string]> = {
  ordnungssystemposition: ['Neue Ordnungssystemposition', 'Mehrere neue Ordnungssystempositionen…'],
  dossier: ['Neues Dossier', 'Mehrere neue Dossiers…'],
  dokument: ['Neues Dokument', 'Mehrere neue Dokumente…'],
  mappe: ['Neue Mappe', 'Mehrere neue Mappen…'],
  vorgang: ['Neuer Vorgang', 'Mehrere neue Vorgänge…'],
  aktivitaet: ['Neue Aktivität', 'Mehrere neue Aktivitäten…'],
};

@Component({
  selector: 'app-workspace',
  imports: [DatePipe, FormsModule, MetadataField, NavigatorTree, NgTemplateOutlet, ContextMenu],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit {
  readonly api = inject(SipApi);
  private readonly injector = inject(Injector);
  private readonly element = inject<ElementRef<HTMLElement>>(ElementRef);
  readonly contextMenu = signal<ContextMenuState | null>(null);
  private readonly hiddenColumns = signal(new Map<string, Set<number>>());
  private contextRow: HTMLTableRowElement | null = null;
  readonly cutNode = signal<{ sipId: string; revision: string; node: SipNode } | null>(null);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  private readonly editors = viewChildren(MetadataField);
  readonly editing = computed(() => this.editors().some((editor) => editor.editing()));
  private readonly pending = signal(new Map<string, PendingEdit>());
  readonly changes = computed(() => [...this.pending()].map(([key, edit]) => ({ key, ...edit })));
  readonly hasChanges = computed(() => this.pending().size > 0);
  readonly packages = signal<Sip[]>([]);
  readonly hasMore = signal(false);
  readonly sip = signal<Sip | null>(null);
  readonly writable = computed(
    () => !!this.sip() && !this.sip()?.read_only && this.sip()?.role !== 'viewer',
  );
  readonly node = signal<NodeDetail | null>(null);
  readonly delivery = signal<NodeDetail | null>(null);
  readonly provenance = signal<NodeDetail | null>(null);
  private readonly pages = signal(new Map<string, NodePage>());
  private readonly expanded = signal(new Set<string>());
  readonly bottomTree = signal<'dossiers' | 'files'>('dossiers');
  readonly positionsOnly = signal(false);
  readonly editor = signal<Editor>('overview');
  readonly tool = signal<Tool>('validation');
  readonly tools: { id: Tool; label: string; icon: string }[] = [
    { id: 'validation', label: 'Validierungshilfe', icon: 'shield-check' },
    { id: 'notes', label: 'Notiz', icon: 'sticky-note' },
    { id: 'sample', label: 'Stichprobe', icon: 'scan-search' },
    { id: 'search', label: 'Suche', icon: 'search' },
    { id: 'changes', label: 'Änderungen', icon: 'file-text' },
    { id: 'history', label: 'Metadaten-Historie', icon: 'clock' },
  ];
  readonly editorKinds = Object.keys(kindNames).filter((kind) => kind !== 'subdossier');
  readonly busy = signal('');
  readonly error = signal('');
  readonly message = signal('');
  readonly mode = signal<Dialog>('open');
  readonly results = signal<SearchResult | null>(null);
  readonly samples = signal<SampleResult | null>(null);
  readonly searchOptions = signal<Partial<Record<string, SearchOption[]>>>({});
  readonly searchKinds = computed(() => Object.keys(this.searchOptions()));
  readonly advanced = signal(false);
  readonly reports = [
    { id: 'classification', label: 'Ordnungssystem' },
    { id: 'dossiers', label: 'Dossierverzeichnis' },
    { id: 'delivery', label: 'Ablieferungsverzeichnis' },
  ];
  readonly checks = signal<ValidationResult | null>(null);
  readonly stats = signal<Statistics | null>(null);
  readonly audit = signal<History | null>(null);
  readonly notes = signal<NotesPage | null>(null);
  readonly notesOwner = signal<string | null>(null);
  readonly severity = signal('');
  readonly checkId = signal('');
  private readonly generation = signal(0);
  private readonly knownNodes = computed(() => {
    const entries = [...this.pages().values()].flatMap((page) => page.items);
    return new Map(entries.map((entry) => [entry.id, entry]));
  });

  readonly availableActions = computed(() => {
    const detail = this.node();
    if (!detail || !this.writable()) return [];
    const kind = detail.node.kind;
    const choices: { id: Action; label: string }[] = [];
    if (
      [
        'ordnungssystemposition',
        'dossier',
        'subdossier',
        'dokument',
        'mappe',
        'vorgang',
        'aktivitaet',
      ].includes(kind)
    ) {
      choices.push(
        { id: 'move-up', label: 'Nach oben verschieben' },
        { id: 'move-down', label: 'Nach unten verschieben' },
        { id: 'move-object', label: 'In anderes Objekt verschieben' },
        { id: 'delete-object', label: 'Objekt löschen' },
      );
    }
    if (['ordnungssystem', 'ordnungssystemposition', 'dossier', 'subdossier'].includes(kind)) {
      choices.push({ id: 'mass-assign', label: 'Massenzuweisung' });
    }
    if (!['datei', 'ordner', 'inhaltsverzeichnis', 'archivischeNotiz'].includes(kind)) {
      choices.push({ id: 'mass-edit', label: 'Massenmutation' });
    }
    if (kind === 'archivischeNotiz') choices.push({ id: 'delete-note', label: 'Notiz löschen' });
    if (kind === 'ordnungssystem' || kind === 'paket')
      choices.push({ id: 'renumber', label: 'Ordnungssystem nummerieren' });
    if (['dossier', 'subdossier', 'dokument', 'mappe'].includes(kind)) {
      choices.push({ id: 'assign-files', label: 'Dateien zuweisen' });
      if (detail.references.length)
        choices.push({ id: 'remove-assignment', label: 'Dateizuweisung entfernen' });
    }
    if (kind === 'dossier' || kind === 'subdossier') {
      choices.push(
        { id: 'create-documents', label: 'Dokumente für Dateien erstellen' },
        { id: 'accumulate-timespans', label: 'Zeiträume kumulieren' },
      );
    }
    if (kind === 'ordner' && detail.can_upload_file)
      choices.push({ id: 'new-folder', label: 'Neuer Ordner' });
    if (
      detail.file_name ||
      (kind === 'ordner' && detail.can_upload_file && detail.node.name !== 'content')
    ) {
      choices.push(
        { id: 'rename-entry', label: 'Umbenennen' },
        { id: 'delete-entry', label: 'Primärdaten löschen' },
      );
    }
    return choices;
  });
  readonly actionTargets = computed(() => {
    const action = this.actionType();
    const selected = this.node();
    if (action === 'remove-assignment') return selected?.references ?? [];
    return [...this.knownNodes().values()].filter(
      (node) =>
        node.id !== selected?.node.id &&
        (action === 'assign-files'
          ? node.kind === 'datei'
          : action === 'mass-assign'
            ? node.kind === 'ordner'
            : [
                'ordnungssystem',
                'ordnungssystemposition',
                'dossier',
                'subdossier',
                'dokument',
                'mappe',
                'vorgang',
              ].includes(node.kind)),
    );
  });

  readonly classification = computed(() =>
    this.findChild(this.delivery()?.node.id, 'ordnungssystem'),
  );
  readonly toc = computed(() => this.findChild('0', 'inhaltsverzeichnis'));
  readonly classificationRows = computed(() => {
    const root = this.classification();
    if (!root) return [];
    return this.tree(
      [root],
      (kind) =>
        kind === 'ordnungssystem' ||
        kind === 'ordnungssystemposition' ||
        (!this.positionsOnly() && recordKinds.has(kind)),
    );
  });
  readonly fileRows = computed(() => {
    const toc = this.toc();
    const sip = this.sip();
    if (!toc || !sip) return [];
    return this.tree(
      [{ ...toc, name: sip.name, kind: 'paket' }],
      (kind) => kind !== 'archivischeNotiz',
    );
  });
  readonly dossierRows = computed(() => {
    const page = this.pages().get(DOSSIERS);
    const rows = this.tree(page?.items ?? [], (kind) => recordKinds.has(kind));
    if (page?.next_offset != null) rows.push({ key: 'more-dossiers', parent: DOSSIERS, depth: 0 });
    return rows;
  });
  readonly crumbs = computed(() => {
    const parts = this.node()?.node.id.split('.') ?? [];
    return parts
      .slice(1, -1)
      .map((_, index) => this.knownNodes().get(parts.slice(0, index + 2).join('.')))
      .filter((node): node is SipNode => !!node);
  });
  readonly overview = computed(() =>
    [this.delivery(), this.provenance()].filter((node): node is NodeDetail => !!node),
  );
  readonly issues = computed(() =>
    (this.checks()?.issues ?? []).filter(
      (issue) =>
        (!this.severity() || issue.severity === this.severity()) &&
        (!this.checkId() || issue.check_id === this.checkId()),
    ),
  );
  readonly statsGroups = computed(() => {
    const stats = this.stats();
    return stats
      ? [
          { title: 'Verzeichnungseinheiten', rows: stats.records },
          { title: 'Technische Angaben', rows: stats.technical },
          { title: 'Ordnungssystempositionen pro Stufe', rows: stats.positionsPerLevel },
          { title: 'Subdossiers pro Stufe', rows: stats.subdossiersPerLevel },
          { title: 'Dokumente nach Dateianzahl', rows: stats.documentsByFileCount },
        ].filter((group) => group.rows.length)
      : [];
  });

  office = '';
  reference = '';
  creator = '';
  systemName = '';
  packageName = '';
  classificationCsv: File | null = null;
  release: NonNullable<CreateSip['release']> = '1.3';
  deliveryType: NonNullable<CreateSip['delivery_type']> = 'GEVER';
  selectedZip: File | null = null;
  selectedMetadata: File | null = null;
  importReadOnly = false;
  query = '';
  sampleMode: 'absolute' | 'percent' = 'absolute';
  sampleSize = 10;
  readonly actionType = signal<Action | ''>('');
  actionTargetsSelected: string[] = [];
  actionName = '';
  actionScheme: 'decimal' | 'hundred' = 'decimal';
  actionKind = 'dossier';
  actionTag = '';
  actionValue = '';
  actionEnd = '';
  positionLevels = 1;
  dossierLevels = 1;
  createDocuments = false;
  includeTop = false;
  propertyTarget: NodeDetail | null = null;
  propertyGroup: 'additional' | 'file' = 'additional';
  propertyRows: NameValue[] = [];
  replacement: File | null = null;
  cleanupKeep = 3;
  cleanupDays = 7;
  readonly cleanup = signal<CleanupPreview | null>(null);
  readonly eventDetails = signal('');
  metsVersion = '2';
  redactPreservation = true;
  searchKind = 'dossier';
  criteria = [{ tag: '', value: '', empty: false }];
  childKind: ChildCreate['kind'] | '' = '';
  childCount = 1;
  childTitle = '';
  noteText = '';
  attachment: File | null = null;
  noteTarget: NotesPage['items'][number] | null = null;
  noteEditText = '';
  contentMode: 'files' | 'folder' | 'replace' | 'replace-many' = 'files';
  contentFiles: File[] = [];
  contentOverwrite = false;

  constructor() {
    afterEveryRender(() => {
      for (const table of this.element.nativeElement.querySelectorAll<HTMLTableElement>('table')) {
        const hidden = this.hiddenColumns().get(this.tableKey(table));
        for (const row of table.rows) {
          for (const cell of row.cells) cell.hidden = hidden?.has(cell.cellIndex) ?? false;
          if (row.parentElement?.tagName === 'THEAD') {
            for (const cell of row.cells) {
              cell.tabIndex = 0;
              cell.setAttribute('aria-haspopup', 'menu');
            }
          } else {
            row.tabIndex = 0;
            row.setAttribute('aria-haspopup', 'menu');
            row.classList.toggle('context-target', row === this.contextRow);
          }
        }
      }
    });
  }

  closeContext() {
    this.contextMenu.set(null);
    this.contextRow = null;
  }

  private showContext(
    event: MouseEvent | KeyboardEvent,
    label: string,
    commands: ContextCommand[],
  ) {
    event.preventDefault();
    event.stopPropagation();
    const target = event.target as HTMLElement;
    const invoker = target.closest<HTMLElement>('button, a, th, tr, h3') ?? target;
    const bounds = invoker.getBoundingClientRect();
    this.contextMenu.set({
      label,
      commands,
      invoker,
      x:
        event instanceof MouseEvent && (event.clientX || event.clientY)
          ? event.clientX
          : bounds.left,
      y:
        event instanceof MouseEvent && (event.clientX || event.clientY)
          ? event.clientY
          : bounds.bottom,
    });
  }

  async openNodeContext(
    context: ItemContext,
    source: 'classification' | 'dossiers' | 'files' | 'section',
  ) {
    const { event, nodeId, parentId } = context;
    event.preventDefault();
    event.stopPropagation();
    const sip = this.sip();
    if (!sip || this.busy() || this.editing()) return;
    const previous = this.node();
    await this.run('Metadaten werden geladen', () => this.openNode(sip, nodeId));
    const detail = this.node();
    if (this.error() || !detail || detail.node.id !== nodeId || this.sip()?.id !== sip.id) return;
    const blocked = this.hasChanges() || !this.writable();
    const ownerId = detail.owner_ids?.find((id) => id === parentId) ?? detail.owner_ids?.[0];
    const commands: ContextCommand[] = [
      { label: 'Im Editor anzeigen', run: () => this.navigate(nodeId) },
      {
        label: 'Im Ordnungssystem anzeigen',
        disabled:
          detail.node.kind === 'datei'
            ? !ownerId
            : !recordKinds.has(detail.node.kind) &&
              !['ordnungssystem', 'ordnungssystemposition'].includes(detail.node.kind),
        run: () => {
          this.bottomTree.set('dossiers');
          this.navigate(ownerId ?? nodeId);
        },
      },
    ];
    if (detail.file_name)
      commands.push({
        label: 'Datei zeigen / herunterladen',
        icon: 'download',
        run: () => this.download(this.api.contentUrl(sip, nodeId)),
      });
    if (source !== 'dossiers' && source !== 'section') {
      for (const kind of detail.allowed_children)
        commands.push(
          {
            label: creationNames[kind]?.[0] ?? 'Neu: ' + this.nameFor(kind),
            disabled: blocked,
            run: () => {
              this.showDialog('child');
              this.childKind = kind;
            },
          },
          {
            label: creationNames[kind]?.[1] ?? 'Mehrere neue ' + this.nameFor(kind) + '…',
            disabled: blocked,
            run: () => {
              this.showDialog('child');
              this.childKind = kind;
              this.childCount = 2;
            },
          },
        );
      for (const choice of this.availableActions())
        commands.push({
          label: choice.label,
          disabled: blocked,
          danger: choice.id.startsWith('delete-'),
          run: () => this.contextAction(choice.id),
        });
      if (this.availableActions().some((action) => action.id === 'move-object'))
        commands.push({
          label: 'Ausschneiden (Verschieben)',
          disabled: blocked,
          run: () => this.cutNode.set({ sipId: sip.id, revision: sip.revision, node: detail.node }),
        });
      const cut = this.cutNode();
      if (cut && cut.node.id !== nodeId)
        commands.push({
          label: 'Einfügen: ' + cut.node.name,
          disabled:
            blocked ||
            cut.sipId !== sip.id ||
            cut.revision !== sip.revision ||
            !detail.allowed_children.includes(
              (cut.node.kind === 'subdossier' ? 'dossier' : cut.node.kind) as ChildCreate['kind'],
            ),
          run: () =>
            this.commitContextAction({
              action: 'move-object',
              node_id: cut.node.id,
              target_ids: [nodeId],
            }),
        });
      if (detail.can_upload_file)
        commands.push({
          label: 'Dateien importieren…',
          icon: 'folder-open',
          disabled: blocked,
          run: () => this.showContent('files'),
        });
      if (source === 'files' && detail.can_upload_file && detail.node.kind === 'ordner')
        commands.push({
          label: 'Ordner importieren…',
          icon: 'folder-open',
          disabled: blocked,
          run: () => this.showContent('folder'),
        });
      if (detail.file_name)
        commands.push({
          label: 'Datei ersetzen…',
          disabled: blocked,
          run: () => this.showContent('replace'),
        });
      if (source === 'files' && detail.node.kind === 'inhaltsverzeichnis')
        commands.push({
          label: 'Dateien ersetzen…',
          disabled: blocked,
          run: () => this.showContent('replace-many'),
        });
      if (
        detail.node.kind === 'datei' &&
        previous &&
        ['dossier', 'subdossier', 'dokument', 'mappe'].includes(previous.node.kind)
      )
        commands.push({
          label: 'Dem zuvor gewählten Objekt zuweisen',
          disabled: blocked,
          run: () =>
            this.commitContextAction({
              action: 'assign-files',
              node_id: previous.node.id,
              target_ids: [nodeId],
            }),
        });
      if (
        previous?.node.kind === 'datei' &&
        ['dossier', 'subdossier', 'dokument', 'mappe'].includes(detail.node.kind)
      )
        commands.push({
          label: 'Zuvor gewählte Datei zuweisen',
          disabled: blocked,
          run: () =>
            this.commitContextAction({
              action: 'assign-files',
              node_id: nodeId,
              target_ids: [previous.node.id],
            }),
        });
      if (ownerId && source === 'classification' && detail.node.kind === 'datei') {
        commands.push({
          label: 'Zuweisung löschen',
          disabled: blocked,
          run: () => {
            if (window.confirm('Zuweisung der Datei zum Objekt löschen?'))
              this.commitContextAction({
                action: 'remove-assignment',
                node_id: ownerId,
                target_ids: [nodeId],
              });
          },
        });
        if (['dossier', 'subdossier'].includes(this.knownNodes().get(ownerId)?.kind ?? ''))
          commands.push({
            label: 'Neues Dokument für diese Datei',
            disabled: blocked,
            run: () =>
              this.commitContextAction({
                action: 'document-for-file',
                node_id: nodeId,
                target_ids: [ownerId],
              }),
          });
      }
      if (
        detail.node.kind === 'ordner' &&
        previous &&
        ['ordnungssystem', 'ordnungssystemposition', 'dossier', 'subdossier'].includes(
          previous.node.kind,
        )
      )
        commands.push({
          label: 'Massenzuweisung auf das zuvor gewählte Objekt…',
          disabled: blocked,
          run: () => this.massAssignContext(previous.node.id, nodeId),
        });
      if (
        previous?.node.kind === 'ordner' &&
        ['ordnungssystem', 'ordnungssystemposition', 'dossier', 'subdossier'].includes(
          detail.node.kind,
        )
      )
        commands.push({
          label: 'Massenzuweisung des zuvor gewählten Ordners…',
          disabled: blocked,
          run: () => {
            this.contextAction('mass-assign');
            this.actionTargetsSelected = [previous.node.id];
          },
        });
    }
    if (detail.can_add_note)
      commands.push({
        label: 'Neue Notiz…',
        icon: 'sticky-note',
        disabled: blocked,
        separator: true,
        run: () => this.showNotes(nodeId),
      });
    commands.push(
      { label: 'Gehe zu Notiz', disabled: !detail.note_count, run: () => this.showNotes(nodeId) },
      {
        label: 'Kopieren',
        icon: 'copy',
        separator: true,
        run: () =>
          this.copyText(
            source === 'dossiers'
              ? [
                  detail.node.name,
                  detail.node.reference,
                  detail.node.timespan,
                  detail.node.appearance,
                ].join('\t')
              : detail.node.name,
          ),
      },
    );
    this.showContext(event, detail.node.name || detail.node.label, commands);
  }

  private contextAction(action: Action) {
    this.actionTargetsSelected = [];
    this.actionName = '';
    this.actionTag = '';
    this.actionValue = '';
    this.actionEnd = '';
    if (
      ![
        'move-up',
        'move-down',
        'delete-object',
        'delete-entry',
        'delete-note',
        'create-documents',
        'accumulate-timespans',
      ].includes(action)
    )
      this.showDialog('actions');
    this.setAction(action);
    if (
      [
        'move-up',
        'move-down',
        'delete-object',
        'delete-entry',
        'delete-note',
        'create-documents',
        'accumulate-timespans',
      ].includes(action)
    )
      this.applyAction();
  }

  private download(url: string) {
    const link = document.createElement('a');
    link.href = url;
    link.click();
  }

  private copyText(text: string) {
    void this.run('Auswahl wird kopiert', async () => {
      await navigator.clipboard.writeText(text);
      this.message.set('Auswahl in die Zwischenablage kopiert.');
    });
  }

  private tableKey(table: HTMLTableElement) {
    const container = table.closest('.results, .editor') ?? this.element.nativeElement;
    const index = [...container.querySelectorAll('table')].indexOf(table);
    return (table.closest('.results') ? this.tool() : this.editor()) + ':' + index;
  }

  @HostListener('contextmenu', ['$event'])
  tableContext(event: MouseEvent | KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (target.closest('input, textarea, select, [contenteditable="true"], app-context-menu'))
      return;
    const table = target.closest<HTMLTableElement>('table');
    if (!table || this.busy()) return;
    const header = target.closest('thead');
    if (header) {
      const key = this.tableKey(table);
      const hidden = this.hiddenColumns().get(key) ?? new Set<number>();
      const cells = [...(table.tHead?.rows[0].cells ?? [])];
      const commands: ContextCommand[] = cells.map((cell, index) => ({
        label: cell.textContent?.trim() || 'Aktionen',
        checked: !hidden.has(index),
        disabled: !hidden.has(index) && cells.length - hidden.size <= 1,
        run: () =>
          this.hiddenColumns.update((all) => {
            const next = new Set(all.get(key));
            if (next.has(index)) next.delete(index);
            else next.add(index);
            return new Map(all).set(key, next);
          }),
      }));
      commands.push(
        {
          label: 'Alle Spalten anzeigen',
          separator: true,
          run: () => this.hiddenColumns.update((all) => new Map(all).set(key, new Set())),
        },
        { label: 'Tabelle kopieren', icon: 'copy', run: () => this.copyTable(table) },
      );
      this.showContext(event, 'Tabellenspalten', commands);
      return;
    }
    const row = target.closest<HTMLTableRowElement>('tbody tr');
    if (!row) return;
    const commands: ContextCommand[] = [];
    this.contextRow = row;
    if (table.closest('.results')) {
      const index = row.sectionRowIndex;
      switch (this.tool()) {
        case 'validation': {
          const issue = this.issues()[index];
          if (!issue) return;
          commands.push({ label: 'Im Editor anzeigen', run: () => this.navigate(issue.node_id) });
          for (const [action, label] of this.fixChoices(issue))
            commands.push({
              label,
              disabled: this.hasChanges() || !this.writable(),
              run: () => this.fixValidation(issue, action),
            });
          break;
        }
        case 'notes': {
          const note = this.notes()?.items[index];
          if (!note) return;
          commands.push(
            {
              label: 'Notiz bearbeiten…',
              disabled: this.hasChanges() || !this.writable(),
              run: () => {
                this.noteTarget = note;
                this.noteEditText = note.text;
                this.showDialog('note-edit');
              },
            },
            {
              label: 'Notiz löschen',
              danger: true,
              disabled: this.hasChanges() || !this.writable(),
              run: () => this.deleteNote(note.node_id),
            },
            { label: 'Objekt anzeigen', run: () => this.navigate(note.owner_id) },
          );
          break;
        }
        case 'sample': {
          const item = this.samples()?.items[index];
          if (item)
            commands.push({ label: 'Im Editor anzeigen', run: () => this.navigate(item.node_id) });
          break;
        }
        case 'search': {
          const item = this.results()?.hits[index];
          if (item)
            commands.push({ label: 'Im Editor anzeigen', run: () => this.navigate(item.node_id) });
          break;
        }
        case 'changes': {
          const change = this.changes()[index];
          if (change)
            commands.push(
              { label: 'Im Editor anzeigen', run: () => this.navigate(change.object.id) },
              {
                label: 'Änderung verwerfen',
                danger: true,
                disabled: this.editing(),
                run: () => this.discardChange(change.key),
              },
            );
          break;
        }
        case 'history': {
          const entry = this.audit()?.items[index];
          if (entry)
            commands.push({
              label: 'Ereignisdetails anzeigen',
              run: () => this.showHistoryEvent(entry.sequence),
            });
          break;
        }
      }
    }
    commands.push({
      label: 'Zeile kopieren',
      icon: 'copy',
      separator: !!commands.length,
      run: () => this.copyText(this.rowText(row)),
    });
    this.showContext(event, 'Tabellenzeile', commands);
  }

  private rowText(row: HTMLTableRowElement) {
    return [...row.cells]
      .filter((cell) => !cell.hidden)
      .map((cell) => (cell.textContent ?? '').trim().replace(/[\t\r\n]+/g, ' '))
      .join('\t');
  }

  private copyTable(table: HTMLTableElement) {
    this.copyText([...table.rows].map((row) => this.rowText(row)).join('\n'));
  }

  @HostListener('keydown', ['$event'])
  contextKey(event: KeyboardEvent) {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))
      this.tableContext(event);
  }

  sectionContextKey(event: Event, nodeId: string) {
    if (
      event instanceof KeyboardEvent &&
      (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))
    )
      void this.openNodeContext({ event, nodeId }, 'section');
  }

  private deleteNote(id: string) {
    const sip = this.sip();
    if (!sip || this.hasChanges() || !window.confirm('Notiz endgültig löschen?')) return;
    void this.run('Notiz wird gelöscht', async () => {
      const updated = await this.api.packageAction(sip, { action: 'delete-note', node_id: id });
      await this.openSip(updated);
      this.tool.set('notes');
      await this.fetchNotes(updated, 0);
    });
  }

  saveNote() {
    const note = this.noteTarget;
    const sip = this.sip();
    if (!note || !sip || !this.noteEditText.trim() || this.hasChanges()) return;
    void this.run('Notiz wird gespeichert', async () => {
      const updated = await this.api.packageAction(sip, {
        action: 'edit-note',
        node_id: note.node_id,
        value: this.noteEditText,
      });
      await this.openSip(updated);
      this.closeDialog();
      this.tool.set('notes');
      await this.fetchNotes(updated, 0);
    });
  }

  private commitContextAction(command: PackageAction) {
    const sip = this.sip();
    if (!sip || this.hasChanges() || !this.writable()) return;
    void this.run('Paketaktion wird ausgeführt', async () => {
      await this.openSip(await this.api.packageAction(sip, command));
      this.message.set('Paketaktion gespeichert.');
    });
  }

  private async massAssignContext(targetId: string, folderId: string) {
    const sip = this.sip();
    if (!sip) return;
    await this.run('Zielobjekt wird geladen', () => this.openNode(sip, targetId));
    if (this.error() || this.node()?.node.id !== targetId) return;
    this.contextAction('mass-assign');
    this.actionTargetsSelected = [folderId];
  }

  showNotes(ownerId: string | null) {
    this.notesOwner.set(ownerId);
    this.notes.set(null);
    this.setTool('notes');
  }

  private showContent(mode: App['contentMode']) {
    this.contentMode = mode;
    this.contentFiles = [];
    this.contentOverwrite = false;
    this.showDialog('content');
  }

  async submitContent() {
    const sip = this.sip();
    const detail = this.node();
    if (!sip || !detail || !this.contentFiles.length || this.hasChanges()) return;
    if (
      (this.contentMode.startsWith('replace') || this.contentOverwrite) &&
      !window.confirm('Bestehende Dateiinhalte ersetzen und Prüfsummen neu berechnen?')
    )
      return;
    await this.run('Dateien werden importiert', async () => {
      let updated: Sip;
      if (this.contentMode === 'replace') {
        updated = await this.api.replaceContent(sip, detail.node.id, this.contentFiles[0]);
      } else {
        const archive: Zippable = Object.create(null);
        for (const file of this.contentFiles) {
          const path =
            this.contentMode === 'folder' || this.contentMode === 'replace-many'
              ? file.webkitRelativePath || file.name
              : file.name;
          if (Object.hasOwn(archive, path))
            throw new Error('Mehrere Dateien haben denselben Importpfad.');
          archive[path] = new Uint8Array(await file.arrayBuffer());
        }
        const bytes = await new Promise<Uint8Array>((resolve, reject) =>
          zip(archive, { level: 0 }, (error, output) => (error ? reject(error) : resolve(output))),
        );
        updated = await this.api.importContent(
          sip,
          detail.node.id,
          new Blob([Uint8Array.from(bytes).buffer], { type: 'application/zip' }),
          this.contentMode === 'replace-many',
          this.contentOverwrite,
        );
      }
      await this.openSip(updated);
      this.closeDialog();
      this.message.set('Dateien und Prüfsummen gespeichert.');
    });
  }

  ngOnInit() {
    void this.run('Pakete werden geladen', () => this.loadPackages());
  }

  hasPrimary(detail: NodeDetail) {
    return detail.fields.some((field) => field.primary);
  }
  hasAdditional(detail: NodeDetail) {
    return (
      this.hasPrimary(detail) &&
      detail.fields.some((field) => !field.primary && field.tag !== 'zusatzDaten')
    );
  }
  nameFor(kind: string) {
    return kindNames[kind] ?? kind;
  }
  fieldsFor(detail: NodeDetail) {
    return detail.fields.map((field) => ({
      field: this.pending().get(this.editKey(detail.node.id, field.tag))?.field ?? field,
      key: [this.generation(), detail.node.id, field.tag].join('-'),
    }));
  }
  value(detail: NodeDetail | null, tag: string) {
    return (
      (detail && this.pending().get(this.editKey(detail.node.id, tag))?.field.values[0]) ??
      detail?.fields.find((field) => field.tag === tag)?.values[0] ??
      '—'
    );
  }

  private findChild(parent: string | undefined, kind: string) {
    return parent
      ? this.pages()
          .get(parent)
          ?.items.find((node) => node.kind === kind)
      : undefined;
  }

  private tree(roots: SipNode[], include: (kind: string) => boolean): TreeRow[] {
    const pages = this.pages();
    const expanded = this.expanded();
    const rows: TreeRow[] = [];
    const visit = (node: SipNode, depth: number, path: string, parentId?: string) => {
      if (!include(node.kind)) return;
      const key = path + '/' + node.id;
      const page = pages.get(node.id);
      const expandable =
        node.has_children &&
        (!page || page.next_offset !== null || page.items.some((child) => include(child.kind)));
      const open = expandable && expanded.has(node.id);
      rows.push({ key, node, depth, expandable, expanded: open, parentId });
      if (open && page) {
        for (const child of page.items) visit(child, depth + 1, key, node.id);
        if (page.next_offset !== null)
          rows.push({ key: key + '/more', parent: node.id, depth: depth + 1 });
      }
    };
    for (const root of roots) visit(root, 0, '');
    return rows;
  }

  private async loadPage(sip: Sip, id: string, offset = 0) {
    const page = await (id === DOSSIERS
      ? this.api.dossiers(sip, offset)
      : this.api.children(sip, id, offset));
    this.pages.update((pages) => {
      const previous = offset ? (pages.get(id)?.items ?? []) : [];
      const items = [
        ...new Map([...previous, ...page.items].map((item) => [item.id, item])).values(),
      ];
      return new Map(pages).set(id, { ...page, items });
    });
    return page;
  }

  private setExpanded(id: string, expanded: boolean) {
    this.expanded.update((previous) => {
      const next = new Set(previous);
      if (expanded) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  toggle(id: string) {
    if (this.expanded().has(id)) {
      this.setExpanded(id, false);
      return;
    }
    const sip = this.sip();
    if (sip)
      void this.run('Navigator wird geladen', async () => {
        if (!this.pages().has(id)) await this.loadPage(sip, id);
        this.setExpanded(id, true);
      });
  }

  moreChildren(id: string) {
    const sip = this.sip();
    const offset = this.pages().get(id)?.next_offset;
    if (sip && offset != null)
      void this.run('Objekte werden geladen', async () => {
        await this.loadPage(sip, id, offset);
      });
  }

  showDialog(mode: Dialog) {
    this.closeContext();
    if (mode === 'child') this.childCount = 1;
    this.error.set('');
    this.selectedZip = null;
    this.selectedMetadata = null;
    this.classificationCsv = null;
    this.mode.set(mode);
    afterNextRender(
      () => {
        const dialog = this.dialog().nativeElement;
        if (!dialog.open) dialog.showModal();
      },
      { injector: this.injector },
    );
  }

  showProperties(detail: NodeDetail, group: 'additional' | 'file') {
    if (this.hasChanges() || this.sip()?.read_only) return;
    this.propertyTarget = detail;
    this.propertyGroup = group;
    this.propertyRows =
      (group === 'additional' ? detail.additional_data : detail.file_properties)?.map((item) => ({
        ...item,
      })) ?? [];
    this.showDialog('properties');
  }

  saveProperties() {
    const detail = this.propertyTarget;
    if (!detail || this.hasChanges()) return;
    void this.change(
      'Eigenschaften werden gespeichert',
      detail.node.id,
      (sip, id) => this.api.updateProperties(sip, id, this.propertyGroup, this.propertyRows),
      () => this.closeDialog(),
    );
  }

  saveReference(detail: NodeDetail, index: number, name: string, value: string) {
    if (!this.hasChanges())
      void this.change(
        'Dateiverweis wird gespeichert',
        detail.node.id,
        (sip, id) => this.api.updateReference(sip, id, index, name, value),
        () => {},
      );
  }

  showActions() {
    if (!this.availableActions().length || this.hasChanges()) return;
    this.actionType.set('');
    this.actionTargetsSelected = [];
    this.actionName = '';
    this.actionTag = '';
    this.showDialog('actions');
  }

  setAction(action: Action | '') {
    this.actionType.set(action);
    this.actionTargetsSelected = [];
    if (action === 'mass-edit' && !this.searchKinds().length) {
      void this.run('Metadatenfelder werden geladen', async () =>
        this.searchOptions.set(await this.api.searchFields()),
      );
    }
  }

  applyAction() {
    const sip = this.sip();
    const node = this.node();
    const action = this.actionType();
    if (!sip || !node || !action || this.hasChanges()) return;
    if (
      ['delete-object', 'delete-entry', 'delete-note', 'mass-edit'].includes(action) &&
      !window.confirm('Diese Paketänderung ausführen?')
    )
      return;
    const command: PackageAction = {
      action,
      node_id: node.node.id,
      target_ids: this.actionTargetsSelected,
      name: this.actionName.trim() || null,
      scheme: this.actionScheme,
      kind: this.actionKind,
      tag: this.actionTag,
      value: this.actionValue,
      end: this.actionEnd,
      position_levels: this.positionLevels,
      dossier_levels: this.dossierLevels,
      create_documents: this.createDocuments,
      include_top: this.includeTop,
    };
    void this.run('Paketaktion wird ausgeführt', async () => {
      const updated = await this.api.packageAction(sip, command);
      await this.openSip(updated);
      this.closeDialog();
      this.message.set('Paketaktion gespeichert.');
    });
  }

  replaceFile(input: HTMLInputElement) {
    const sip = this.sip();
    const id = this.node()?.node.id;
    const file = this.replacement;
    if (!sip || !id || !file || this.hasChanges() || !window.confirm('Dateiinhalt ersetzen?'))
      return;
    void this.run('Datei wird ersetzt', async () => {
      const updated = await this.api.replaceContent(sip, id, file);
      this.sip.set(updated);
      await this.openNode(updated, id);
      this.clearResults();
      this.replacement = null;
      input.value = '';
      this.message.set('Datei ersetzt und Prüfsumme neu berechnet.');
    });
  }

  closeDialog() {
    const dialog = this.dialog().nativeElement;
    if (dialog.open) dialog.close();
  }

  morePackages() {
    void this.run('Pakete werden geladen', () => this.loadPackages(this.packages().length));
  }

  private async loadPackages(offset = 0) {
    const items = await this.api.list(offset);
    this.packages.set(offset ? [...this.packages(), ...items] : items);
    this.hasMore.set(items.length === 50);
  }

  create() {
    if (!this.confirmLeaving()) return;
    void this.run('SIP wird erstellt', async () => {
      if (this.classificationCsv && this.classificationCsv.size > 524288) {
        this.error.set('Die CSV-Datei darf höchstens 512 KB gross sein.');
        return;
      }
      const created = await this.api.create({
        delivering_office: this.office,
        name: this.packageName.trim() || undefined,
        classification_csv: this.classificationCsv
          ? await this.classificationCsv.text()
          : undefined,
        reference: this.reference.trim() || null,
        creator: this.creator.trim() || null,
        system_name: this.systemName.trim() || undefined,
        release: this.release,
        delivery_type: this.deliveryType,
      });
      await this.afterCreate(created);
    });
  }

  import() {
    const file = this.selectedZip;
    if (file && this.confirmLeaving())
      void this.run('ZIP wird importiert', async () => {
        await this.afterCreate(await this.api.upload(file, this.importReadOnly));
      });
  }

  importMetadata() {
    const file = this.selectedMetadata;
    if (file && this.confirmLeaving())
      void this.run('Metadatendatei wird geöffnet', async () => {
        await this.afterCreate(await this.api.uploadMetadata(file));
      });
  }

  private async afterCreate(created: Sip) {
    this.packages.update((packages) => [created, ...packages]);
    await this.openSip(created);
    this.closeDialog();
    await this.loadPackages();
    this.message.set('SIP geöffnet.');
  }

  select(id: string) {
    if (!this.confirmLeaving()) return;
    void this.run('SIP wird geöffnet', async () => {
      await this.openSip(await this.api.get(id));
      this.closeDialog();
    });
  }

  closeSip() {
    if (this.confirmLeaving()) this.resetSip();
  }

  @HostListener('window:beforeunload', ['$event'])
  protectPending(event: BeforeUnloadEvent) {
    if (this.hasChanges() || this.editing()) {
      event.preventDefault();
      event.returnValue = true;
    }
  }

  private confirmLeaving() {
    return (
      !(this.hasChanges() || this.editing()) ||
      window.confirm('Ungespeicherte Änderungen verwerfen und Paket verlassen?')
    );
  }

  private resetSip() {
    this.closeContext();
    this.cutNode.set(null);
    this.notesOwner.set(null);
    this.pending.set(new Map());
    this.sip.set(null);
    this.node.set(null);
    this.delivery.set(null);
    this.provenance.set(null);
    this.pages.set(new Map());
    this.expanded.set(new Set());
    this.clearResults();
    this.editor.set('overview');
    this.tool.set('validation');
    this.error.set('');
    this.message.set('');
  }

  private async openSip(sip: Sip) {
    this.resetSip();
    try {
      const detail = await this.loadNavigation(sip);
      this.setNode(detail);
      this.sip.set(sip);
      this.bottomTree.set('dossiers');
      this.query = '';
    } catch (error) {
      this.resetSip();
      throw error;
    }
  }

  private async loadNavigation(sip: Sip) {
    this.pages.set(new Map());
    this.expanded.set(new Set());
    const [root] = await Promise.all([
      this.api.node(sip, '0'),
      this.loadPage(sip, '0'),
      this.loadPage(sip, DOSSIERS),
    ]);
    const delivery = this.findChild('0', 'ablieferung');
    const toc = this.toc();
    if (delivery) {
      const [detail] = await Promise.all([
        this.api.node(sip, delivery.id),
        this.loadPage(sip, delivery.id),
      ]);
      this.delivery.set(detail);
      const provenance = this.findChild(delivery.id, 'provenienz');
      this.provenance.set(provenance ? await this.api.node(sip, provenance.id) : null);
    }
    const roots = [this.classification(), toc].filter((entry): entry is SipNode => !!entry);
    await Promise.all(roots.map((entry) => this.loadPage(sip, entry.id)));
    for (const entry of roots) this.setExpanded(entry.id, true);
    return root;
  }

  navigate(id: string) {
    if (this.editing() && !window.confirm('Feldeingabe noch nicht geprüft. Verwerfen?')) return;
    const sip = this.sip();
    if (sip) void this.run('Metadaten werden geladen', () => this.openNode(sip, id));
  }

  private async openNode(sip: Sip, id: string) {
    const detail = await this.api.node(sip, id);
    await this.reveal(sip, id);
    this.setNode(detail);
    if (['ordner', 'datei', 'inhaltsverzeichnis'].includes(detail.node.kind))
      this.bottomTree.set('files');
  }

  private async reveal(sip: Sip, id: string) {
    const parts = id.split('.');
    for (let i = 1; i < parts.length; i++) {
      const parent = parts.slice(0, i).join('.');
      const next = parts.slice(0, i + 1).join('.');
      const page = this.pages().get(parent) ?? (await this.loadPage(sip, parent));
      if (!page.items.some((entry) => entry.id === next) && page.next_offset !== null) {
        const offset = Math.floor(Number(parts[i]) / 50) * 50;
        const needed = await this.api.children(sip, parent, offset);
        const missing = needed.items.find((entry) => entry.id === next);
        if (missing)
          this.pages.update((pages) =>
            new Map(pages).set(parent, { ...page, items: [...page.items, missing] }),
          );
      }
      this.setExpanded(parent, true);
    }
  }

  private setNode(detail: NodeDetail) {
    this.generation.update((value) => value + 1);
    this.node.set(detail);
    this.editor.set(
      ['paket', 'ablieferung', 'provenienz', 'inhaltsverzeichnis'].includes(detail.node.kind)
        ? 'overview'
        : 'object',
    );
    this.childKind = '';
    this.childTitle = '';
    this.noteText = '';
    this.attachment = null;
    this.replacement = null;
  }

  nodeOfKind(kind: string) {
    const current = this.node()?.node;
    if (current?.kind === kind || (current?.kind === 'subdossier' && kind === 'dossier'))
      return current;
    return [...this.knownNodes().values()].find((entry) => entry.kind === kind);
  }

  selectKind(kind: string) {
    const entry = this.nodeOfKind(kind);
    if (entry) this.navigate(entry.id);
  }

  setEditor(editor: Editor) {
    if (
      editor !== this.editor() &&
      this.editing() &&
      !window.confirm('Feldeingabe noch nicht geprüft. Verwerfen?')
    )
      return;
    this.editor.set(editor);
    const sip = this.sip();
    if (editor === 'statistics' && sip && !this.stats()) {
      void this.run('Statistik wird geladen', async () =>
        this.stats.set(await this.api.statistics(sip)),
      );
    }
  }

  private editKey(id: string, tag: string) {
    return id + ':' + tag;
  }

  stageField(detail: NodeDetail, editor: MetadataField, edit: FieldEdit) {
    const sip = this.sip();
    const saved = detail.fields.find((field) => field.tag === edit.tag);
    if (!sip || !saved) return;
    const key = this.editKey(detail.node.id, edit.tag);
    const previous = this.pending().get(key);
    if (!previous && this.pending().size >= 200) {
      this.error.set(
        'Speichern Sie die 200 offenen Änderungen, bevor Sie weitere Felder bearbeiten.',
      );
      return;
    }
    const indices = previous?.request.edit.source_indices;
    const request: MetadataEdit = {
      node_id: detail.node.id,
      edit: {
        ...edit,
        ...(indices && edit.source_indices
          ? {
              source_indices: edit.source_indices.map((index) => (index < 0 ? -1 : indices[index])),
            }
          : {}),
      },
    };
    void this.run('Änderung wird geprüft', async () => {
      const preview = await this.api.previewMetadata(sip, request);
      this.pending.update((pending) => {
        const updated = new Map(pending);
        if (preview.changed)
          updated.set(key, {
            request,
            field: preview.field,
            saved,
            time: new Date(),
            object: detail.node,
          });
        else updated.delete(key);
        return updated;
      });
      editor.cancel();
      this.tool.set('changes');
      this.message.set(
        preview.changed
          ? 'Änderung vorgemerkt. Mit Speichern wird sie ins SIP geschrieben.'
          : 'Keine Änderung gegenüber dem gespeicherten Wert.',
      );
    });
  }

  discardChange(key: string) {
    this.pending.update((pending) => {
      const next = new Map(pending);
      next.delete(key);
      return next;
    });
  }

  discardChanges() {
    if (this.hasChanges() && window.confirm('Alle offenen Metadatenänderungen verwerfen?')) {
      this.pending.set(new Map());
      this.message.set('Offene Änderungen verworfen.');
    }
  }

  saveChanges() {
    const sip = this.sip();
    if (!sip || !this.hasChanges() || this.editing()) return;
    const edits = this.changes().map((item) => item.request);
    void this.run('Änderungen werden gespeichert', async () => {
      const result = await this.api.commitMetadata(sip, edits);
      this.sip.set({ ...sip, revision: result.revision });
      this.updateDetails(result.nodes);
      this.pending.set(new Map());
      this.clearResults();
      this.message.set('Änderungen im SIP und in der Metadaten-Historie gespeichert.');
    });
  }

  private updateDetails(details: NodeDetail[]) {
    const updated = new Map(details.map((detail) => [detail.node.id, detail]));
    for (const store of [this.node, this.delivery, this.provenance]) {
      const current = store();
      if (current && updated.has(current.node.id)) store.set(updated.get(current.node.id)!);
    }
    this.pages.update(
      (pages) =>
        new Map(
          [...pages].map(([key, page]) => [
            key,
            { ...page, items: page.items.map((node) => updated.get(node.id)?.node ?? node) },
          ]),
        ),
    );
  }

  private changeRows() {
    return [
      ['Zeit', 'Objekt', 'Objektname', 'Metadatum', 'Wert', 'Gespeicherter Wert'],
      ...this.changes().map((change) => [
        change.time.toLocaleString('de-CH'),
        change.object.label,
        change.object.name,
        change.field.label,
        change.field.values.join(' · '),
        change.saved.values.join(' · '),
      ]),
    ];
  }

  copyChanges() {
    const rows = this.changeRows();
    void this.run('Änderungen werden kopiert', async () => {
      await navigator.clipboard.writeText(
        rows
          .map((row) => row.map((value) => value.replace(/[\t\r\n]+/g, ' ')).join('\t'))
          .join('\n'),
      );
      this.message.set('Änderungen in die Zwischenablage kopiert.');
    });
  }

  exportChanges() {
    this.exportTable('Aenderungen', this.changeRows());
  }

  private exportTable(name: string, rows: string[][]) {
    const quote = (value: string) => {
      const safe = /^[\s]*[=+@-]/.test(value) ? "'" + value : value;
      return '"' + safe.replaceAll('"', '""') + '"';
    };
    const csv = rows.map((row) => row.map(quote).join(';')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = name + '.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  addChild() {
    const kind = this.childKind;
    const id = this.node()?.node.id;
    if (kind && id && !this.hasChanges())
      void this.change(
        'Objekt wird erstellt',
        id,
        (sip, nodeId) =>
          this.api.addChild(sip, nodeId, {
            kind,
            count: this.childCount,
            title: this.childTitle.trim() || null,
          }),
        () => this.closeDialog(),
        true,
      );
  }

  addNote() {
    const id = this.node()?.node.id;
    if (id && !this.hasChanges())
      void this.change(
        'Notiz wird gespeichert',
        id,
        (sip, nodeId) => this.api.addNote(sip, nodeId, this.noteText),
        () => {
          this.noteText = '';
        },
        true,
      );
  }

  attach(input: HTMLInputElement) {
    const file = this.attachment;
    const id = this.node()?.node.id;
    if (file && id && !this.hasChanges())
      void this.change(
        'Datei wird gespeichert',
        id,
        (sip, nodeId) => this.api.attach(sip, nodeId, file),
        () => {
          this.attachment = null;
          input.value = '';
        },
        true,
      );
  }

  private async change(
    status: string,
    id: string,
    action: (sip: Sip, id: string) => Promise<NodeDetail>,
    onSaved: () => void,
    structureChanged = false,
  ) {
    const sip = this.sip();
    if (!sip) return;
    await this.run(status, async () => {
      const updated = await action(sip, id);
      const next = { ...sip, revision: updated.revision };
      this.sip.set(next);
      this.updateDetails([updated]);
      onSaved();
      if (updated.revision === sip.revision) return;
      const selectedId = this.node()?.node.id;
      this.clearResults();
      this.message.set('Im SIP und in der Metadaten-Historie gespeichert.');
      if (structureChanged) {
        await this.loadNavigation(next);
        if (selectedId) {
          await this.reveal(next, selectedId);
          if (this.node()?.node.has_children) {
            await this.loadPage(next, selectedId);
            this.setExpanded(selectedId, true);
          }
        }
      }
      if (this.tool() === 'notes') await this.fetchNotes(next, 0);
      if (this.tool() === 'history') await this.fetchHistory(next, 0);
    });
  }

  private clearResults() {
    this.results.set(null);
    this.samples.set(null);
    this.checks.set(null);
    this.stats.set(null);
    this.audit.set(null);
    this.notes.set(null);
    this.severity.set('');
    this.checkId.set('');
  }

  setTool(tool: Tool) {
    this.tool.set(tool);
    const sip = this.sip();
    if (!sip) return;
    if (tool === 'history' && !this.audit())
      void this.run('Historie wird geladen', () => this.fetchHistory(sip, 0));
    if (tool === 'notes' && !this.notes())
      void this.run('Notizen werden geladen', () => this.fetchNotes(sip, 0));
  }

  search() {
    const sip = this.sip();
    const query = this.query.trim();
    if (sip && query) {
      this.tool.set('search');
      this.advanced.set(false);
      void this.run('Metadaten werden durchsucht', async () =>
        this.results.set(await this.api.search(sip, query)),
      );
    }
  }

  setAdvanced(enabled: boolean) {
    this.advanced.set(enabled);
    this.results.set(null);
    if (enabled && !this.searchKinds().length) {
      void this.run('Suchfelder werden geladen', async () =>
        this.searchOptions.set(await this.api.searchFields()),
      );
    }
  }

  changeSearchKind(kind: string) {
    this.searchKind = kind;
    this.criteria = [{ tag: '', value: '', empty: false }];
  }

  addCriterion() {
    if (this.criteria.length < 3)
      this.criteria = [...this.criteria, { tag: '', value: '', empty: false }];
  }

  advancedSearch() {
    const sip = this.sip();
    const criteria = this.criteria.filter((item) => item.tag && (item.empty || item.value.trim()));
    if (sip && criteria.length)
      void this.run('Metadaten werden durchsucht', async () => {
        this.results.set(await this.api.advancedSearch(sip, { kind: this.searchKind, criteria }));
      });
  }

  drawSample() {
    const sip = this.sip();
    if (sip)
      void this.run('Stichprobe wird gezogen', async () =>
        this.samples.set(await this.api.sample(sip, this.sampleMode, this.sampleSize)),
      );
  }

  exportSample() {
    const rows = this.samples()?.items ?? [];
    this.exportTable('Stichprobe', [
      ['Aktenzeichen', 'Titel'],
      ...rows.map((row) => [row.reference, row.name]),
    ]);
  }

  validate(mode: 'metadata' | 'full') {
    const sip = this.sip();
    if (sip)
      void this.run('SIP wird validiert', async () => {
        this.checkId.set('');
        this.severity.set('');
        this.checks.set(await this.api.validate(sip, mode));
        this.editor.set('validation');
        this.tool.set('validation');
      });
  }

  fixChoices(issue: ValidationIssue) {
    return Object.entries(issue.fixes);
  }

  fixValidation(issue: ValidationIssue, action: string) {
    const sip = this.sip();
    if (!sip || this.hasChanges() || !window.confirm('Diese Validierungskorrektur anwenden?'))
      return;
    void this.run('Korrektur wird angewendet', async () => {
      const updated = await this.api.fixValidation(sip, issue, action);
      await this.openSip(updated);
      this.checks.set(await this.api.validate(updated, 'full'));
      this.editor.set('validation');
      this.tool.set('validation');
    });
  }

  showCleanup() {
    this.cleanup.set(null);
    this.showDialog('cleanup');
  }

  previewCleanup() {
    const sip = this.sip();
    if (!sip || this.hasChanges()) return;
    this.cleanup.set(null);
    void this.run('Bereinigungsvorschau wird erstellt', async () =>
      this.cleanup.set(await this.api.cleanupPreview(sip, this.cleanupKeep, this.cleanupDays)),
    );
  }

  applyCleanup() {
    const sip = this.sip();
    const preview = this.cleanup();
    if (
      !sip ||
      !preview ||
      this.hasChanges() ||
      !window.confirm('Die angezeigten älteren Sicherungskopien endgültig löschen?')
    )
      return;
    void this.run('Sicherungskopien werden bereinigt', async () => {
      const result = await this.api.applyCleanup(sip, preview);
      this.cleanup.set(null);
      this.closeDialog();
      await this.fetchHistory(sip, 0);
      this.message.set(result.removed_bytes + ' Bytes aus älteren Sicherungskopien freigegeben.');
    });
  }

  showHistoryEvent(sequence: number) {
    const sip = this.sip();
    if (sip)
      void this.run('Ereignis wird geladen', async () => {
        this.eventDetails.set(JSON.stringify(await this.api.historyEvent(sip, sequence), null, 2));
        this.showDialog('event');
      });
  }

  moreHistory() {
    const sip = this.sip();
    const offset = this.audit()?.next_offset;
    if (sip && offset != null)
      void this.run('Historie wird geladen', () => this.fetchHistory(sip, offset));
  }

  private async fetchHistory(sip: Sip, offset: number) {
    const next = await this.api.history(sip, offset);
    this.audit.set({
      ...next,
      items: [...(offset ? (this.audit()?.items ?? []) : []), ...next.items],
    });
  }

  moreNotes() {
    const sip = this.sip();
    const offset = this.notes()?.next_offset;
    if (sip && offset != null)
      void this.run('Notizen werden geladen', () => this.fetchNotes(sip, offset));
  }

  private async fetchNotes(sip: Sip, offset: number) {
    const next = await this.api.notes(sip, offset, this.notesOwner());
    this.notes.set({
      ...next,
      items: [...(offset ? (this.notes()?.items ?? []) : []), ...next.items],
    });
  }

  private async run(status: string, action: () => Promise<void>) {
    if (this.busy()) return;
    this.closeContext();
    this.busy.set(status);
    this.error.set('');
    this.message.set('');
    try {
      await action();
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.busy.set('');
    }
  }
}
