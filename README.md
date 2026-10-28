# AI検索 露出モニター（給湯器交換の匠）

毎週月曜の朝に、Gemini・ChatGPT・Claude・Perplexity に「寝屋川で給湯器交換のおすすめは？」などを自動で質問し、
当社が推薦されたか・何番目か・類似名称の他社と混同されていないかを Googleスプレッドシートにまとめます。
最後にAIが改善提案も書きます。

- 費用：API代のみ 月800〜1,000円前後（20問×4AI×週1回）
- 設定作業：約40〜60分（**パソコン推奨**。スマホでもできますが画面が狭く大変です）

---

## スプレッドシートに出るもの

| シート | 内容 |
|---|---|
| 回答ログ | 質問ごと・AIごとの結果。当社言及／順位／推薦された業者の並び／類似名称混同／電話番号／要確認／参照URL／回答本文 |
| 週次サマリー | AIごとの言及率・平均順位・当社サイトが参照された数・混同件数・費用 |
| レポート | 前回との比較、よく推薦される他社、当社が出なかった質問、改善提案 |

「言及」＝回答の本文に当社名が出た。「当社サイト参照」＝当社サイトが情報源として使われた（名前は出なくても）。

---

## 設定手順

### ① APIキーを4つ取る（約20分）

| AI | 取得場所 | やること |
|---|---|---|
| Gemini | https://aistudio.google.com/apikey | 「APIキーを作成」→ 支払い情報を登録（Google検索付き回答は有料プランが必要。月5,000回までは検索無料） |
| ChatGPT | https://platform.openai.com/api-keys | キー作成 → Billing で 5ドル チャージ |
| Claude | https://console.anthropic.com/ | 「API Keys」でキー作成 → 5ドル チャージ |
| Perplexity | https://www.perplexity.ai/account/api | キー作成 → 5ドル チャージ |

キーは「sk-…」などの長い文字列です。**他人に見せないでください。** メモ帳に一時保存しておき、④で貼り付けたら消してOKです。
1つだけでも動きます（未登録のAIは自動でスキップ）。ただし Gemini は分析にも使うので必須です。

### ② スプレッドシートを準備（約10分）

1. https://sheets.new で新しいスプレッドシートを作成（名前は「AI検索モニター」など）
2. メニュー「拡張機能」→「Apps Script」
3. 最初から入っているコードを全部消して、`apps_script/Code.gs` の中身を貼り付け
4. `const TOKEN = 'ここを好きな合言葉に変更';` の行の中身を、英数字20文字くらいの合言葉に変える（例：`takumi2026monitorXyZ8`）
5. 保存（💾）→ 右上「デプロイ」→「新しいデプロイ」
6. 歯車アイコンから「ウェブアプリ」を選び、
   - 次のユーザーとして実行：**自分**
   - アクセスできるユーザー：**全員**
7. 「デプロイ」→ Googleアカウントの許可画面が出たら許可（「安全ではないページ」と出たら「詳細」→「移動」）
8. 表示される **ウェブアプリのURL**（`https://script.google.com/macros/s/…/exec`）をコピー

### ③ GitHubにアップロード（約10分）

1. https://github.com でアカウント作成（無料）
2. 右上「＋」→「New repository」
   - 名前：`ai-monitor`
   - **Private** を選択（重要）
   - 「Create repository」
3. 「uploading an existing file」をクリック
4. このzipを解凍したフォルダの**中身すべて**をドラッグ＆ドロップ
   - `.github` フォルダは隠しフォルダなので見えない場合があります。
     Macは Finder で `⌘ + Shift + .`、Windowsはエクスプローラーの「表示」→「隠しファイル」で表示されます
5. 「Commit changes」
6. 隠しフォルダがうまく上がらなかった場合：「Add file」→「Create new file」で、ファイル名欄に
   `.github/workflows/ai-monitor.yml` と入力し、同じ名前のファイルの中身を貼り付けて「Commit changes」

### ④ キーを登録（約5分）

リポジトリの「Settings」→ 左の「Secrets and variables」→「Actions」→「New repository secret」で、次の6つを1つずつ登録：

| Name（そのまま入力） | Secret（値） |
|---|---|
| `GEMINI_API_KEY` | Geminiのキー |
| `OPENAI_API_KEY` | ChatGPTのキー |
| `ANTHROPIC_API_KEY` | Claudeのキー |
| `PERPLEXITY_API_KEY` | Perplexityのキー |
| `SHEETS_WEBHOOK_URL` | ②でコピーしたウェブアプリのURL |
| `SHEETS_TOKEN` | ②で決めた合言葉 |

### ⑤ お試し実行（約5分）

1. リポジトリの「Actions」タブ → 左の「AI検索モニター」→ 右の「Run workflow」
2. 「お試しで質問数を減らす場合の数」に `2` と入れて「Run workflow」（2問×4AIで数円）
3. 数分待って緑のチェック ✅ になればOK。スプレッドシートに3つのシートができています
4. 赤い ❌ の場合は、クリックして「AIに質問して記録」を開き、エラー文をClaudeに貼って相談してください

あとは**毎週月曜 9時過ぎに自動で動きます**。

---

## よくある変更

すべて GitHub 上で `config.yaml` を開き、鉛筆マーク（Edit）で編集 →「Commit changes」するだけです。

- **質問を増やす・変える**：`questions:` の行を追加・編集（1問あたり週4〜6円）
- **電話番号チェックを有効にする**：`official_phones:` に正しいフリーダイヤルを入力
- **同じ質問を2回ずつ聞く**：`repeats: 2`（精度は上がるが費用2倍）
- **あるAIを止める**：そのAIの `enabled: false`
- **実行曜日・時刻を変える**：`.github/workflows/ai-monitor.yml` の `cron`（時刻はUTC。日本時間−9時間）

## モデル名のエラーが出たら

AI各社はモデル名をよく変えます。「model not found」等のエラーが出たら、`config.yaml` の `model:` を各社の最新の安いモデル名に書き換えてください（Claudeに聞けば調べます）。

## 注意

- APIの回答はアプリ版（chatgpt.com 等）と完全には一致しません。**傾向をつかむ道具**として使ってください
- Copilotは対象外です（一般向けAPIがないため）。CopilotとChatGPTは同じBing系なので、ChatGPTの結果が目安になります
- 結果は GitHub の `results/` フォルダにもCSVで残ります（スプレッドシートが消えてもバックアップになります）
