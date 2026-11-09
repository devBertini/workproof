export class CryptoService {
  encrypt(plain: string): string { return plain.split('').reverse().join(''); }
}
