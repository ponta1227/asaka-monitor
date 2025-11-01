# 朝霞市 公共施設（総合体育館）監視・LINE通知（スターター）

> ✅ これは**最小構成（MVP）**です。まずは「空きがあったらURL付きでLINE通知する」まで到達させ、
> その後 DOM セレクタや抽出ロジックを少しずつ精密化してください。

---

## 0) 重要なお知らせ（通知方法について）
- 以前よく使われていた **LINE Notify は 2025-03-31 にサービス終了** しました。代替として本スターターでは
  **LINE Messaging API（公式アカウント）** の Push Message を使います。

## 1) 事前準備（1回限り）
1. LINE Developers コンソールで **Messaging API チャネル** を作成し、チャネルアクセストークン（長期）を取得
   - 公式ドキュメント：`https://developers.line.biz/`
2. 同コンソールのチャネル「Basic settings」に表示される **Your user ID** を控える（開発者自身に送る場合）
3. このリポジトリをローカルに展開し、`.env.example` を `.env` にコピーして、以下を設定
   ```env
   LINE_CHANNEL_ACCESS_TOKEN=...
   LINE_TO_USER_ID=Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
4. Python 3.11 以上を用意し、依存をインストール
   ```bash
   pip install -r requirements.txt
   python -m playwright install --with-deps
   ```

## 2) まずは **手動テスト**
```bash
python monitor.py
```
- 成功すると、条件に合う空きがページ内に検出された場合、あなたの LINE に通知されます。
- うまく検出できない場合は、以下の手順で **セレクタを取得** して `navigate_to_subfacility()` を編集します。
  ```bash
  python -m playwright codegen https://www.pf-yoyaku.com/User/asaka/Home
  ```
  画面操作を記録して「空き照会・申込」→「一覧から探す」→「全施設」→「総合体育館（４月１日～）」→「メイン/サブアリーナ」→「空き状況」
  までの **クリックを再現** し、右側に出る Python コードのセレクタをコピペして関数を置き換えてください。

## 3) GitHub Actions で **30分ごと**に自動実行
1. この一式を GitHub に push
2. リポジトリの Settings > Secrets and variables > Actions に以下を追加
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_TO_USER_ID`
3. 同梱のワークフローファイル（`.github/workflows/monitor.yml`）が **UTC で 30分おき**に起動します。

> 注: 空き状況の閲覧は多くの自治体でログイン不要ですが、**サイトの利用規約やアクセス負荷**には十分配慮してください。

## 4) 次の改善アイデア（任意）
- DOM を解析して **日付・時間帯を正確に抽出**（セルに含まれる「○ / × / 空き / 予約可」等を列・行見出しと紐づけ）
- **重複通知防止**（例：検出ハッシュを Gist / S3 / Firestore に永続化）
- **障害通知**（例：異常時にスタックトレースを送る）
- **祝日判定の厳密化**（`jpholiday` は 2026年まで動作確認済み）

---

### ライセンス
MIT
