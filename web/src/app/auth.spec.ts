import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { Auth, sessionInterceptor } from './auth';

describe('Authentication client', () => {
  const identity = { subject: 'archivist', display_name: 'Archivist' };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([sessionInterceptor])),
        provideHttpClientTesting(),
      ],
    });
  });
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('restores a session and treats only 401 as anonymous', async () => {
    const auth = TestBed.inject(Auth);
    const http = TestBed.inject(HttpTestingController);
    const restore = auth.restore();
    http.expectOne('/api/v1/auth/session').flush(identity);
    await restore;
    expect(auth.identity()).toEqual(identity);
    const anonymous = auth.restore();
    http.expectOne('/api/v1/auth/session').flush({}, { status: 401, statusText: 'Unauthorized' });
    await anonymous;
    expect(auth.identity()).toBeNull();
    const failure = auth.restore();
    const assertion = expect(failure).rejects.toMatchObject({ status: 503 });
    http.expectOne('/api/v1/auth/session').flush({}, { status: 503, statusText: 'Unavailable' });
    await assertion;
  });

  it('logs in, does not expire on rejected credentials, and logs out', async () => {
    const auth = TestBed.inject(Auth);
    const http = TestBed.inject(HttpTestingController);
    const login = auth.login('archivist', 'test-password');
    const request = http.expectOne('/api/v1/auth/login');
    expect(request.request.body).toEqual({ username: 'archivist', password: 'test-password' });
    request.flush(identity);
    await login;
    expect(auth.identity()).toEqual(identity);
    const rejected = auth.login('archivist', 'wrong');
    const assertion = expect(rejected).rejects.toMatchObject({ status: 401 });
    http.expectOne('/api/v1/auth/login').flush({}, { status: 401, statusText: 'Unauthorized' });
    await assertion;
    expect(auth.expired()).toBe(false);
    expect(auth.identity()).toEqual(identity);
    const logout = auth.logout();
    http.expectOne('/api/v1/auth/logout').flush(null);
    await logout;
    expect(auth.identity()).toBeNull();
  });

  it('returns to login on an expired workspace request and propagates the error', async () => {
    const auth = TestBed.inject(Auth);
    auth.identity.set(identity);
    const request = firstValueFrom(TestBed.inject(HttpClient).get('/api/v1/packages'));
    const assertion = expect(request).rejects.toMatchObject({ status: 401 });
    TestBed.inject(HttpTestingController)
      .expectOne('/api/v1/packages')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    await assertion;
    expect(auth.identity()).toBeNull();
    expect(auth.expired()).toBe(true);
  });

  it('sends the CSRF cookie on mutations, not on reads', async () => {
    document.cookie = 'XSRF-TOKEN=test-csrf; path=/';
    const http = TestBed.inject(HttpTestingController);
    const client = TestBed.inject(HttpClient);
    const mutation = firstValueFrom(client.post('/api/v1/packages', {}));
    const request = http.expectOne('/api/v1/packages');
    expect(request.request.headers.get('X-XSRF-TOKEN')).toBe('test-csrf');
    request.flush({});
    await mutation;
    const read = firstValueFrom(client.get('/api/v1/packages'));
    const get = http.expectOne('/api/v1/packages');
    expect(get.request.headers.has('X-XSRF-TOKEN')).toBe(false);
    get.flush([]);
    await read;
    document.cookie = 'XSRF-TOKEN=; Max-Age=0; path=/';
  });
});
