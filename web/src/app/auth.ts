import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { catchError, firstValueFrom, throwError } from 'rxjs';
import type { components } from './api.types';

export type Identity = components['schemas']['Identity'];

@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly http = inject(HttpClient);
  readonly identity = signal<Identity | null>(null);
  readonly expired = signal(false);

  async restore() {
    try {
      this.identity.set(await firstValueFrom(this.http.get<Identity>('/api/v1/auth/session')));
    } catch (error) {
      if (!(error instanceof HttpErrorResponse) || error.status !== 401) throw error;
      this.identity.set(null);
    }
  }

  async login(username: string, password: string) {
    const identity = await firstValueFrom(
      this.http.post<Identity>('/api/v1/auth/login', { username, password }),
    );
    this.expired.set(false);
    this.identity.set(identity);
  }

  async logout() {
    await firstValueFrom(this.http.post<void>('/api/v1/auth/logout', {}));
    this.identity.set(null);
  }
}

export const sessionInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(Auth);
  return next(request).pipe(
    catchError((error: unknown) => {
      if (
        error instanceof HttpErrorResponse &&
        error.status === 401 &&
        request.url.startsWith('/api/') &&
        !request.url.endsWith('/auth/login') &&
        auth.identity()
      ) {
        auth.expired.set(true);
        auth.identity.set(null);
      }
      return throwError(() => error);
    }),
  );
};
