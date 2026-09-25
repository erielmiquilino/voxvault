## 1. Aplicativo

- [x] 1.1 Módulo `registro.rs`, compartilhado com o esquema `voxvault:`: leitura de um valor de `HKCU` sem expandir variáveis e no tamanho que tiver, escrita com o tipo informado e aviso `WM_SETTINGCHANGE`; verificar com um teste que grava e relê um valor de mais de 8 mil caracteres com `%USERPROFILE%`, igual e com o mesmo tipo, e com outro que um valor que não é texto dá erro, e não ausência.
- [x] 1.2 Módulo `caminho.rs`: a pasta `%USERPROFILE%\.voxvault\bin` com cópias de `voxvault.exe` e `voxvault-mcp.exe` e a entrada no fim do `PATH` do usuário, e a remoção; verificar com testes que a pasta entra uma vez só e no fim, que uma entrada com outra caixa ou barra no fim conta como presente, e que remover tira só ela e deixa o resto igual e na mesma ordem.
- [x] 1.3 Chamar a integração ao fim de todo preparo bem-sucedido, sem falhar o preparo, e tratar `--remover-do-path` antes da instância única; verificar com clippy e os testes do aplicativo passando.
- [ ] 1.4 Gancho de desinstalação executando `--remover-do-path` depois da recusa por gravação e fora do modo de atualização; verificar lendo o gancho gerado no instalador do ensaio.

## 2. Verificação instalada

- [ ] 2.1 Instalar o ensaio por cima da 0.1.3 nesta máquina; verificar que a atualização cria a pasta com os dois executáveis, que `HKCU\Environment\Path` ganha a pasta no fim com o resto igual, e que num terminal aberto depois `voxvault list` funciona pelo nome e `python` não é o do ambiente.
- [ ] 2.2 Executar `voxvault-app.exe --remover-do-path` e depois refazer o preparo; verificar que a entrada sai do `PATH` sem mexer no resto, e volta com o preparo.

## 3. Documentação

- [ ] 3.1 README (linha de comando e desinstalação) e notas permanentes da release dizendo o que muda no `PATH` do usuário, e `docs/estado-da-implementacao.md`; verificar que os três dizem o mesmo.
