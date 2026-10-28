/**
 * AI検索 露出モニター → Googleスプレッドシート受信用
 *
 * 使い方：
 * 1. 新しいGoogleスプレッドシートを作る
 * 2. メニュー「拡張機能」→「Apps Script」を開き、このコードを丸ごと貼り付けて保存
 * 3. 下の TOKEN を好きな合言葉（英数字20文字くらい）に変更
 * 4. 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」
 *      次のユーザーとして実行：自分 ／ アクセスできるユーザー：全員
 * 5. 表示された「ウェブアプリのURL」と TOKEN を GitHub の Secrets に登録
 */
const TOKEN = 'ここを好きな合言葉に変更';

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.token !== TOKEN) return out({ ok: false, error: 'token mismatch' });

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(data.sheet);
    if (!sh) {
      sh = ss.insertSheet(data.sheet);
      sh.appendRow(data.header);
      sh.getRange(1, 1, 1, data.header.length).setFontWeight('bold').setBackground('#e8f0fe');
      sh.setFrozenRows(1);
    }
    const rows = (data.rows || []).map(r => r.map(v => (typeof v === 'string' ? v.slice(0, 45000) : v)));
    if (rows.length) {
      sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
    }
    return out({ ok: true, added: rows.length });
  } catch (err) {
    return out({ ok: false, error: String(err) });
  }
}

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
