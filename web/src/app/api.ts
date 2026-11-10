import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import type { components } from './api.types';

type Contract = components['schemas'];
export type Sip = Contract['PackageSummary'];
export type CreateSip = Contract['PackageCreate'];
export type NodeDetail = Contract['NodeDetail'];
export type NodePage = Contract['NodePage'];
export type SipNode = Contract['Node'];
export type NotesPage = Contract['NotesPage'];
export type Field = Contract['MetadataField'];
export type FieldEdit = Contract['FieldEdit'];
export type MetadataEdit = Contract['MetadataEdit'];
export type MetadataPreview = Contract['MetadataPreview'];
export type MetadataCommit = Contract['MetadataCommit'];
export type SearchResult = Contract['SearchResult'];
export type AdvancedSearch = Contract['AdvancedSearch'];
export type SearchOption = Contract['SearchOption'];
export type SampleResult = Contract['SampleResult'];
export type ValidationResult = Contract['ValidationResult'];
export type ValidationIssue = Contract['ValidationIssue'];
export type Statistics = Contract['Statistics'];
export type History = Contract['History'];
export type CleanupPreview = Contract['CleanupPreview'];
export type ChildCreate = Contract['ChildCreate'];
export type PackageAction = Contract['PackageAction'];
export type NameValue = Contract['NameValue'];

const base = '/api/v1/packages';

@Injectable({ providedIn: 'root' })
export class SipApi {
  private readonly http = inject(HttpClient);

  list(offset: number) {
    return firstValueFrom(this.http.get<Sip[]>(base, { params: { offset } }));
  }

  create(input: CreateSip) {
    return firstValueFrom(this.http.post<Sip>(base, input));
  }

  upload(file: File, readOnly = false) {
    return firstValueFrom(
      this.http.post<Sip>(base + '/import', file, {
        headers: { 'Content-Type': 'application/zip' },
        params: {
          name: file.name.replace(/\.zip$/i, '').slice(0, 255) || 'imported-sip',
          read_only: readOnly,
        },
      }),
    );
  }

  uploadMetadata(file: File) {
    return firstValueFrom(
      this.http.post<Sip>(base + '/import-metadata', file, {
        headers: { 'Content-Type': 'application/xml' },
        params: { name: file.name.slice(0, 255) },
      }),
    );
  }

  get(id: string) {
    return firstValueFrom(this.http.get<Sip>(this.packageUrl(id)));
  }

  node(sip: Sip, id: string) {
    return firstValueFrom(this.http.get<NodeDetail>(this.nodeUrl(sip, id), this.match(sip)));
  }

  children(sip: Sip, id: string, offset = 0) {
    return firstValueFrom(
      this.http.get<NodePage>(this.nodeUrl(sip, id) + '/children', {
        ...this.match(sip),
        params: { offset },
      }),
    );
  }

  dossiers(sip: Sip, offset = 0) {
    return firstValueFrom(
      this.http.get<NodePage>(this.packageUrl(sip.id) + '/dossiers', {
        ...this.match(sip),
        params: { offset },
      }),
    );
  }

  previewMetadata(sip: Sip, edit: MetadataEdit) {
    return firstValueFrom(
      this.http.post<MetadataPreview>(
        this.packageUrl(sip.id) + '/metadata/preview',
        edit,
        this.match(sip),
      ),
    );
  }

  commitMetadata(sip: Sip, edits: MetadataEdit[]) {
    return firstValueFrom(
      this.http.post<MetadataCommit>(
        this.packageUrl(sip.id) + '/metadata/commit',
        { edits },
        this.match(sip),
      ),
    );
  }

  update(sip: Sip, id: string, input: FieldEdit) {
    return firstValueFrom(
      this.http.patch<NodeDetail>(this.nodeUrl(sip, id) + '/field', input, this.match(sip)),
    );
  }

  addChild(sip: Sip, id: string, input: ChildCreate) {
    return firstValueFrom(
      this.http.post<NodeDetail>(this.nodeUrl(sip, id) + '/children', input, this.match(sip)),
    );
  }

  addNote(sip: Sip, id: string, text: string) {
    return firstValueFrom(
      this.http.post<NodeDetail>(this.nodeUrl(sip, id) + '/notes', { text }, this.match(sip)),
    );
  }

  attach(sip: Sip, id: string, file: File) {
    return firstValueFrom(
      this.http.post<NodeDetail>(this.nodeUrl(sip, id) + '/attachments', file, {
        headers: this.match(sip).headers.set('Content-Type', 'application/octet-stream'),
        params: { name: file.name },
      }),
    );
  }

  updateProperties(sip: Sip, id: string, group: 'additional' | 'file', items: NameValue[]) {
    return firstValueFrom(
      this.http.put<NodeDetail>(
        this.nodeUrl(sip, id) + '/properties/' + group,
        { items },
        this.match(sip),
      ),
    );
  }

  updateReference(sip: Sip, id: string, index: number, name: string, value: string) {
    return firstValueFrom(
      this.http.patch<NodeDetail>(
        this.nodeUrl(sip, id) + '/reference-attribute',
        {
          index,
          name,
          value,
        },
        this.match(sip),
      ),
    );
  }

  packageAction(sip: Sip, command: PackageAction) {
    return firstValueFrom(
      this.http.post<Sip>(this.packageUrl(sip.id) + '/actions', command, this.match(sip)),
    );
  }

  replaceContent(sip: Sip, id: string, file: File) {
    return firstValueFrom(
      this.http.put<Sip>(this.nodeUrl(sip, id) + '/content', file, {
        headers: this.match(sip).headers.set('Content-Type', 'application/octet-stream'),
      }),
    );
  }

  importContent(sip: Sip, id: string, archive: Blob, replace = false, overwrite = false) {
    return firstValueFrom(
      this.http.post<Sip>(this.nodeUrl(sip, id) + '/content-import', archive, {
        headers: this.match(sip).headers.set('Content-Type', 'application/zip'),
        params: { replace, overwrite },
      }),
    );
  }

  search(sip: Sip, q: string) {
    return firstValueFrom(
      this.http.get<SearchResult>(this.packageUrl(sip.id) + '/search', {
        ...this.match(sip),
        params: { q },
      }),
    );
  }

  searchFields() {
    return firstValueFrom(this.http.get<Record<string, SearchOption[]>>('/api/v1/search-fields'));
  }

  advancedSearch(sip: Sip, criteria: AdvancedSearch) {
    return firstValueFrom(
      this.http.post<SearchResult>(
        this.packageUrl(sip.id) + '/search/advanced',
        criteria,
        this.match(sip),
      ),
    );
  }

  sample(sip: Sip, mode: 'absolute' | 'percent', size: number) {
    return firstValueFrom(
      this.http.get<SampleResult>(this.packageUrl(sip.id) + '/sample', {
        ...this.match(sip),
        params: { mode, size },
      }),
    );
  }

  metadataUrl(sip: Sip) {
    return this.packageUrl(sip.id) + '/metadata.xml';
  }

  reportUrl(sip: Sip, report: string) {
    return this.packageUrl(sip.id) + '/reports/' + report;
  }

  validate(sip: Sip, mode: 'metadata' | 'full') {
    return firstValueFrom(
      this.http.post<ValidationResult>(this.packageUrl(sip.id) + '/validation', null, {
        ...this.match(sip),
        params: { mode },
      }),
    );
  }

  fixValidation(sip: Sip, issue: ValidationIssue, action: string) {
    return firstValueFrom(
      this.http.post<Sip>(
        this.packageUrl(sip.id) + '/validation/fix',
        {
          check_id: issue.check_id,
          node_id: issue.node_id,
          path: issue.path,
          action,
        },
        this.match(sip),
      ),
    );
  }

  statistics(sip: Sip) {
    return firstValueFrom(
      this.http.get<Statistics>(this.packageUrl(sip.id) + '/statistics', this.match(sip)),
    );
  }

  notes(sip: Sip, offset: number, ownerId?: string | null) {
    return firstValueFrom(
      this.http.get<NotesPage>(this.packageUrl(sip.id) + '/notes', {
        ...this.match(sip),
        params: { offset, ...(ownerId ? { owner_id: ownerId } : {}) },
      }),
    );
  }

  history(sip: Sip, offset: number) {
    return firstValueFrom(
      this.http.get<History>(this.packageUrl(sip.id) + '/history', { params: { offset } }),
    );
  }

  historyEvent(sip: Sip, sequence: number) {
    return firstValueFrom(
      this.http.get<Record<string, unknown>>(
        this.packageUrl(sip.id) + '/history/event/' + sequence,
      ),
    );
  }

  cleanupPreview(sip: Sip, keep: number, days: number) {
    return firstValueFrom(
      this.http.get<CleanupPreview>(this.packageUrl(sip.id) + '/storage/cleanup', {
        ...this.match(sip),
        params: { keep_count: keep, minimum_age_days: days },
      }),
    );
  }

  applyCleanup(sip: Sip, preview: CleanupPreview) {
    return firstValueFrom(
      this.http.post<{ removed_bytes: number }>(
        this.packageUrl(sip.id) + '/storage/cleanup',
        preview,
        this.match(sip),
      ),
    );
  }

  preservationUrl(sip: Sip, format: 'premis' | 'bundle', version: string, redacted: boolean) {
    return (
      this.packageUrl(sip.id) +
      '/history/' +
      format +
      '?' +
      new URLSearchParams({
        version,
        redacted: String(redacted),
      })
    );
  }

  exportUrl(sip: Sip) {
    return this.packageUrl(sip.id) + '/export';
  }

  contentUrl(sip: Sip, id: string) {
    return this.nodeUrl(sip, id) + '/content?revision=' + encodeURIComponent(sip.revision);
  }

  private packageUrl(id: string) {
    return base + '/' + encodeURIComponent(id);
  }

  private nodeUrl(sip: Sip, id: string) {
    return this.packageUrl(sip.id) + '/nodes/' + encodeURIComponent(id);
  }

  private match(sip: Sip) {
    return { headers: new HttpHeaders({ 'If-Match': '"' + sip.revision + '"' }) };
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (typeof error.error?.detail === 'string') return error.error.detail;
    if (Array.isArray(error.error?.detail)) {
      return error.error.detail
        .map(
          (issue: { loc: string[]; msg: string }) =>
            issue.loc.slice(1).join('.') + ' · ' + issue.msg,
        )
        .join(' ');
    }
    if (error.status === 0) return 'Die Package-Turbo-API ist nicht erreichbar.';
  }
  return 'Der Vorgang konnte nicht abgeschlossen werden. Prüfen Sie das Serverprotokoll und versuchen Sie es erneut.';
}
