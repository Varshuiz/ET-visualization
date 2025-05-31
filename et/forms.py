from django import forms

class UploadFileForm(forms.Form):
    file = forms.FileField(
        widget=forms.FileInput(attrs={
            'accept': '.csv',
            'id': 'fileInput',
            'style': 'position: absolute; left: -9999px;'
        }),
        help_text='Upload a CSV file containing weather data from ACIS'
    )
    
    def clean_file(self):
        file = self.cleaned_data['file']
        if file:
            if not file.name.endswith('.csv'):
                raise forms.ValidationError('Please upload a CSV file.')
            if file.size > 10 * 1024 * 1024:  # 10MB limit
                raise forms.ValidationError('File size should not exceed 10MB.')
        return file