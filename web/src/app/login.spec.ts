import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Login } from './login';
import { Auth } from './auth';
import { SipApi } from './api';

describe('Login screen', () => {
  const identity = { subject: 'archivist', display_name: 'Archivist' };

  async function setup(restored = false) {
    const auth = {
      identity: signal<typeof identity | null>(null),
      expired: signal(false),
      restore: vi.fn(async () => {
        if (restored) auth.identity.set(identity);
      }),
      login: vi.fn(async () => {
        auth.identity.set(identity);
      }),
      logout: vi.fn(async () => {
        auth.identity.set(null);
      }),
    };
    const list = vi.fn(async () => []);
    await TestBed.configureTestingModule({
      imports: [Login],
      providers: [
        { provide: Auth, useValue: auth },
        { provide: SipApi, useValue: { list } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(Login);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, auth, list };
  }

  it('does not load package data before login and clears the password after submission', async () => {
    const { fixture, auth, list } = await setup();
    expect(list).not.toHaveBeenCalled();
    expect(fixture.nativeElement.querySelector('input[type=password]').autocomplete).toBe(
      'current-password',
    );
    fixture.componentInstance.username = 'archivist';
    fixture.componentInstance.password = 'test-password';
    await fixture.componentInstance.login();
    fixture.detectChanges();
    await fixture.whenStable();
    expect(auth.login).toHaveBeenCalledWith('archivist', 'test-password');
    expect(fixture.componentInstance.password).toBe('');
    expect(list).toHaveBeenCalledOnce();
    expect(fixture.nativeElement.querySelector('app-workspace')).not.toBeNull();
    await fixture.componentInstance.logout();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('app-workspace')).toBeNull();
  });

  it('shows login failures and does not render the workspace', async () => {
    const { fixture, auth, list } = await setup();
    auth.login.mockRejectedValueOnce(
      new HttpErrorResponse({
        status: 401,
        error: { detail: 'Benutzername oder Passwort ist falsch.' },
      }),
    );
    fixture.componentInstance.username = 'archivist';
    fixture.componentInstance.password = 'wrong';
    await fixture.componentInstance.login();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role=alert]').textContent).toContain(
      'Benutzername oder Passwort ist falsch.',
    );
    expect(fixture.componentInstance.password).toBe('');
    expect(list).not.toHaveBeenCalled();
  });

  it('restores existing sessions before loading the workspace', async () => {
    const { fixture, list } = await setup(true);
    expect(list).toHaveBeenCalledOnce();
    expect(fixture.nativeElement.textContent).toContain('Archivist (archivist)');
  });

  it('allows cancelling logout with unsaved edits', async () => {
    const { fixture, auth } = await setup(true);
    const workspace = fixture.componentInstance.workspace()!;
    vi.spyOn(workspace, 'hasChanges').mockReturnValue(true);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await fixture.componentInstance.logout();
    expect(auth.logout).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
