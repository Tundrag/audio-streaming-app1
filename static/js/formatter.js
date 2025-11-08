function formatPythonCode(code) {
    const lines = code.trim().split('\n');
    let formattedLines = [];
    let indentLevel = 0;
    
    const increaseIndentAfter = [
        'class',
        'def',
        'if',
        'else:',
        'elif',
        'for',
        'while',
        'try:',
        'except',
        'finally:',
        'with'
    ];

    lines.forEach(line => {
        let content = line.trim();
        
        if (!content) {
            formattedLines.push('');
            return;
        }

        formattedLines.push('    '.repeat(indentLevel) + content);

        if (increaseIndentAfter.some(keyword => content.startsWith(keyword)) || 
            content.endsWith(':')) {
            indentLevel++;
        }

        if (content.startsWith('return') || content.startsWith('break')) {
            indentLevel = Math.max(0, indentLevel - 1);
        }
    });

    // Add proper spacing between classes and functions
    const finalLines = [];
    formattedLines.forEach((line, i) => {
        finalLines.push(line);
        if (line.trim().startsWith('class ') && formattedLines[i + 1]?.trim()) {
            finalLines.push('', '');
        } else if (line.trim().startsWith('def ') && formattedLines[i + 1]?.trim()) {
            finalLines.push('');
        }
    });

    return finalLines.join('\n');
}

document.addEventListener('DOMContentLoaded', () => {
    const formatter = document.createElement('div');
    formatter.innerHTML = `
        <div style="position: fixed; top: 20px; right: 20px; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <button id="toggleFormatter">Toggle Python Formatter</button>
            <div id="formatterTools" style="display: none;">
                <textarea id="codeInput" placeholder="Paste Python code here..." 
                    style="width: 300px; height: 200px; margin: 10px 0;"></textarea>
                <br>
                <button id="formatButton">Format</button>
                <button id="copyButton">Copy</button>
                <textarea id="codeOutput" readonly 
                    style="width: 300px; height: 200px; margin-top: 10px;"></textarea>
            </div>
        </div>
    `;
    document.body.appendChild(formatter);

    document.getElementById('toggleFormatter').onclick = () => {
        const tools = document.getElementById('formatterTools');
        tools.style.display = tools.style.display === 'none' ? 'block' : 'none';
    };

    document.getElementById('formatButton').onclick = () => {
        const input = document.getElementById('codeInput');
        const output = document.getElementById('codeOutput');
        output.value = formatPythonCode(input.value);
    };

    document.getElementById('copyButton').onclick = () => {
        const output = document.getElementById('codeOutput');
        output.select();
        document.execCommand('copy');
        alert('Copied!');
    };
});

window.formatMyPythonCode = code => {
    return formatPythonCode(code);
};