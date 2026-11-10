import { Component, OnInit, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { App } from './app';
import { Auth } from './auth';
import { errorMessage } from './api';

@Component({
  selector: 'app-root',
  imports: [FormsModule, App],
  templateUrl: './login.html',
  styleUrl: './login.css',
})
export class Login implements OnInit {
  readonly auth = inject(Auth);
  readonly workspace = viewChild(App);
  readonly checking = signal(true);
  readonly busy = signal(false);
  readonly error = signal('');
  username = '';
  password = '';

  async ngOnInit() {
    await this.restore();
  }

  async restore() {
    this.checking.set(true);
    this.error.set('');
    try {
      await this.auth.restore();
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.checking.set(false);
    }
  }

  async login() {
    if (this.busy() || !this.username || !this.password) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.auth.login(this.username, this.password);
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.password = '';
      this.busy.set(false);
    }
  }

  async logout() {
    if (
      (this.workspace()?.hasChanges() || this.workspace()?.editing()) &&
      !window.confirm('Nicht gespeicherte Änderungen gehen verloren. Wirklich abmelden?')
    )
      return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.auth.logout();
    } catch (error) {
      this.error.set(errorMessage(error));
    } finally {
      this.busy.set(false);
    }
  }
}
