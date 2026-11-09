import { describe, it, expect } from 'vitest';

// describe('commented out', () => { it('ghost', () => {}); });
/* it('also a ghost', () => {}) */
describe('CryptoService', () => {
  it('encrypts and decrypts a key', () => {
    expect({ a: 1 }).toEqual({ a: 1 }); // braces ) ( in code
  });

  describe('with a wrong master key', () => {
    it('fails to decrypt', () => {
      const text = "it('inside a string', () => {})";
      expect(/it\('x'\)/.test(text)).toBe(false);
    });
    it.skip('is slow', () => {});
  });

  it.todo('rotates the master key');
  it(`reads a template without an expression`, () => {});
});

it('top level case', () => {});
