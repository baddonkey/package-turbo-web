import { ApplicationConfig } from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { sessionInterceptor } from './auth';

export const appConfig: ApplicationConfig = {
  providers: [provideHttpClient(withInterceptors([sessionInterceptor]))],
};
