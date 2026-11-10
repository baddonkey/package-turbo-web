import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { NodeDetail, Sip, SipApi } from './api';

describe('SIP API client', () => {
  const sip: Sip = {
    id: 'workspace-id',
    name: 'Test SIP',
    read_only: false,
    delivery_type: 'GEVER',
    schema_version: '5.1',
    revision: 'abc',
  };
  const node: NodeDetail = {
    node: {
      id: '0.1',
      name: 'Test',
      label: 'Dossier',
      kind: 'dossier',
      has_children: false,
      reference: '',
      timespan: '',
      appearance: '',
    },
    parent_id: '0',
    fields: [],
    references: [],
    allowed_children: [],
    can_add_note: true,
    can_upload_file: true,
    file_name: null,
    revision: 'next',
  };
  const endpoint = '/api/v1/packages/workspace-id/nodes/0.1';

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('sends an exact metadata revision and repeated-row identities', async () => {
    const result = TestBed.inject(SipApi).update(sip, '0.1', {
      tag: 'titel',
      values: ['Second', 'First'],
      source_indices: [1, 0],
    });
    const request = TestBed.inject(HttpTestingController).expectOne(endpoint + '/field');
    expect(request.request.method).toBe('PATCH');
    expect(request.request.headers.get('If-Match')).toBe('"abc"');
    expect(request.request.body.source_indices).toEqual([1, 0]);
    request.flush(node);
    expect(await result).toEqual(node);
  });

  it('uploads a content file as binary data with the original filename', async () => {
    const file = new File(['document'], 'file with spaces.txt');
    const result = TestBed.inject(SipApi).attach(sip, '0.1', file);
    const request = TestBed.inject(HttpTestingController).expectOne(
      (request) => request.url === endpoint + '/attachments',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.headers.get('Content-Type')).toBe('application/octet-stream');
    expect(request.request.headers.get('If-Match')).toBe('"abc"');
    expect(request.request.params.get('name')).toBe(file.name);
    expect(request.request.body).toBe(file);
    request.flush(node);
    expect(await result).toEqual(node);
  });
});
